"""Spotify Library Manager — GUI."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from config import Config, ConfigError
from preview_window import PreviewWindow
from spotify_client import OperationResult, SpotifyClient, Track

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

SETTINGS_FILE = Path(__file__).parent / ".user_settings.json"
LOG_FILE = Path(__file__).parent / "app.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
)


class SpotifyManagerApp(ctk.CTk):
    SPOTIFY_GREEN = "#1DB954"
    SPOTIFY_GREEN_HOVER = "#1AA34A"
    DANGER_RED = "#E53935"
    DANGER_RED_HOVER = "#C62828"

    TIME_RANGES = {
        "За 4 недели": "short_term",
        "За 6 месяцев": "medium_term",
        "За всё время": "long_term",
    }

    def __init__(self) -> None:
        super().__init__()
        self.title("Spotify Library Manager")
        self.geometry("900x720")
        self.minsize(800, 650)

        self._client: SpotifyClient | None = None
        self._busy = False
        self._cached_top_tracks: list[Track] = []
        self._playlists_cache: list[dict] = []

        self._build_ui()
        self._load_settings()
        self.after(100, self._init_spotify_async)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(20, 10))

        ctk.CTkLabel(
            header,
            text="Spotify Library Manager",
            font=ctk.CTkFont(size=26, weight="bold"),
        ).pack(side="left")

        self._user_label = ctk.CTkLabel(
            header,
            text="Подключение...",
            font=ctk.CTkFont(size=13),
            text_color=("gray30", "gray70"),
        )
        self._user_label.pack(side="right")

        settings = ctk.CTkFrame(self)
        settings.pack(fill="x", padx=24, pady=10)
        settings.columnconfigure((1, 3), weight=1)

        ctk.CTkLabel(settings, text="Период:", font=ctk.CTkFont(size=13)).grid(
            row=0, column=0, padx=(15, 8), pady=15, sticky="w"
        )
        self._time_var = ctk.StringVar(value="За всё время")
        ctk.CTkOptionMenu(
            settings,
            values=list(self.TIME_RANGES.keys()),
            variable=self._time_var,
            width=160,
        ).grid(row=0, column=1, padx=8, pady=15, sticky="w")

        ctk.CTkLabel(settings, text="Кол-во треков:", font=ctk.CTkFont(size=13)).grid(
            row=0, column=2, padx=(20, 8), pady=15, sticky="w"
        )
        self._limit_var = ctk.StringVar(value="50")
        ctk.CTkEntry(settings, textvariable=self._limit_var, width=80).grid(
            row=0, column=3, padx=8, pady=15, sticky="w"
        )

        main_actions = ctk.CTkFrame(self, fg_color="transparent")
        main_actions.pack(fill="x", padx=24, pady=8)

        self._preview_btn = ctk.CTkButton(
            main_actions,
            text="Предпросмотр топа",
            height=44,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=self.SPOTIFY_GREEN,
            hover_color=self.SPOTIFY_GREEN_HOVER,
            command=self._preview_top,
        )
        self._preview_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))

        self._quick_add_btn = ctk.CTkButton(
            main_actions,
            text="Быстро добавить в библиотеку",
            height=44,
            font=ctk.CTkFont(size=14),
            command=self._quick_add_to_library,
        )
        self._quick_add_btn.pack(side="left", expand=True, fill="x", padx=6)

        playlist_frame = ctk.CTkFrame(self)
        playlist_frame.pack(fill="x", padx=24, pady=8)
        playlist_frame.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            playlist_frame,
            text="Плейлисты",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, columnspan=3, padx=15, pady=(12, 4), sticky="w")

        ctk.CTkLabel(playlist_frame, text="Плейлист:").grid(
            row=1, column=0, padx=(15, 8), pady=(4, 12), sticky="w"
        )
        self._playlist_var = ctk.StringVar(value="— загрузка —")
        self._playlist_menu = ctk.CTkOptionMenu(
            playlist_frame,
            values=["— загрузка —"],
            variable=self._playlist_var,
            width=300,
        )
        self._playlist_menu.grid(row=1, column=1, padx=8, pady=(4, 12), sticky="ew")

        ctk.CTkButton(
            playlist_frame,
            text="Обновить",
            width=90,
            command=self._refresh_playlists_async,
        ).grid(row=1, column=2, padx=(8, 15), pady=(4, 12))

        playlist_actions = ctk.CTkFrame(playlist_frame, fg_color="transparent")
        playlist_actions.grid(
            row=2, column=0, columnspan=3, padx=15, pady=(0, 12), sticky="ew"
        )

        ctk.CTkButton(
            playlist_actions,
            text="Создать новый плейлист",
            command=self._create_playlist_dialog,
        ).pack(side="left", expand=True, fill="x", padx=(0, 6))

        ctk.CTkButton(
            playlist_actions,
            text="Добавить топ в плейлист",
            fg_color=self.SPOTIFY_GREEN,
            hover_color=self.SPOTIFY_GREEN_HOVER,
            command=self._add_top_to_playlist,
        ).pack(side="left", expand=True, fill="x", padx=6)

        danger = ctk.CTkFrame(self, fg_color=("#FFE5E5", "#3A1010"))
        danger.pack(fill="x", padx=24, pady=8)

        ctk.CTkLabel(
            danger,
            text="Опасная зона",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=self.DANGER_RED,
        ).pack(side="left", padx=15, pady=10)

        ctk.CTkButton(
            danger,
            text="Очистить ВСЮ библиотеку",
            fg_color=self.DANGER_RED,
            hover_color=self.DANGER_RED_HOVER,
            command=self._clear_library_dialog,
        ).pack(side="right", padx=15, pady=10)

        # Прогресс-бар (скрыт по умолчанию)
        self._progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._progress_label = ctk.CTkLabel(
            self._progress_frame, text="", font=ctk.CTkFont(size=11), anchor="w"
        )
        self._progress_label.pack(fill="x")
        self._progress_bar = ctk.CTkProgressBar(self._progress_frame)
        self._progress_bar.set(0)
        self._progress_bar.pack(fill="x", pady=(2, 0))

        # Лог
        log_header = ctk.CTkFrame(self, fg_color="transparent")
        log_header.pack(fill="x", padx=24, pady=(8, 0))
        ctk.CTkLabel(
            log_header, text="Лог операций", font=ctk.CTkFont(size=13, weight="bold")
        ).pack(side="left")
        ctk.CTkButton(
            log_header, text="Экспорт лога", width=110, command=self._export_log
        ).pack(side="right")
        ctk.CTkButton(
            log_header, text="Очистить", width=90, command=self._clear_log
        ).pack(side="right", padx=6)

        self._log_text = ctk.CTkTextbox(self, height=180, font=ctk.CTkFont(size=12))
        self._log_text.pack(fill="both", expand=True, padx=24, pady=(4, 16))

    # ---------- утилиты ----------

    def _log(self, message: str, level: str = "info") -> None:
        ts = time.strftime("%H:%M:%S")
        self._log_text.insert("end", f"[{ts}] {message}\n")
        self._log_text.see("end")
        getattr(logging, level, logging.info)(message)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self._preview_btn, self._quick_add_btn):
            btn.configure(state=state)

    def _show_progress(self, show: bool = True) -> None:
        if show:
            self._progress_frame.pack(fill="x", padx=24, pady=(8, 4))
        else:
            self._progress_frame.pack_forget()
            self._progress_bar.set(0)
            self._progress_label.configure(text="")

    def _update_progress(self, done: int, total: int, message: str) -> None:
        def _do():
            self._progress_label.configure(text=message)
            if total > 0:
                self._progress_bar.set(done / total)
            else:
                self._progress_bar.set(0)

        self.after(0, _do)

    def _run_async(self, target, *args, on_done=None) -> None:
        """Запустить операцию в фоновом потоке с управлением busy-state."""
        if self._busy:
            self._log("Дождись окончания текущей операции", "warning")
            return

        self._set_busy(True)
        self._show_progress(True)

        def worker():
            try:
                result = target(*args)
                if on_done:
                    # default arg захватывает значение в момент создания lambda
                    self.after(0, lambda r=result: on_done(r))
            except Exception as exc:
                logging.exception("Operation failed")
                err_msg = str(exc)
                self.after(0, lambda msg=err_msg: self._log(f"Ошибка: {msg}", "error"))
            finally:
                self.after(
                    0, lambda: (self._set_busy(False), self._show_progress(False))
                )

        threading.Thread(target=worker, daemon=True).start()

    # ---------- инициализация ----------

    def _init_spotify_async(self) -> None:
        self._log("Инициализация Spotify...")

        def on_done(result):
            client, user = result
            self._client = client
            name = user.get("display_name") or user.get("id", "?")
            self._user_label.configure(text=f"Подключён: {name}")
            self._log(f"Подключён как {name}")
            self._refresh_playlists_async()

        self._set_busy(True)

        def worker():
            try:
                config = Config.load()
                client = SpotifyClient(config)
                user = client.me()
                self.after(0, lambda c=client, u=user: on_done((c, u)))
            except ConfigError as exc:
                err_msg = str(exc)
                self.after(0, lambda msg=err_msg: self._show_config_error(msg))
            except Exception as exc:
                logging.exception("Init failed")
                err_msg = str(exc)
                self.after(
                    0,
                    lambda msg=err_msg: self._log(
                        f"Ошибка авторизации: {msg}", "error"
                    ),
                )
            finally:
                self.after(0, lambda: self._set_busy(False))

        threading.Thread(target=worker, daemon=True).start()

    def _show_config_error(self, msg: str) -> None:
        self._log(msg, "error")
        messagebox.showerror(
            "Конфигурация",
            f"{msg}\n\n1. Скопируй .env.example → .env\n"
            "2. Заполни SPOTIFY_CLIENT_ID и SPOTIFY_CLIENT_SECRET\n"
            "3. Перезапусти приложение",
        )

    # ---------- топ-треки ----------

    def _check_ready(self) -> bool:
        if not self._client:
            messagebox.showwarning("Подождите", "Spotify ещё подключается")
            return False
        return True

    def _get_inputs(self) -> tuple[str, int] | None:
        try:
            limit = int(self._limit_var.get())
            if not 1 <= limit <= 200:
                raise ValueError("Лимит должен быть от 1 до 200")
        except ValueError as exc:
            messagebox.showerror("Ошибка", f"Некорректное число треков: {exc}")
            return None
        time_range = self.TIME_RANGES[self._time_var.get()]
        return time_range, limit

    def _preview_top(self) -> None:
        if not self._check_ready():
            return
        inputs = self._get_inputs()
        if not inputs:
            return
        time_range, limit = inputs

        self._log(f"Загружаю топ-{limit} ({self._time_var.get()})...")
        client = self._client
        assert client is not None  # _check_ready гарантирует

        def fetch():
            return client.get_top_tracks(time_range, limit)

        def on_done(tracks: list[Track]):
            self._cached_top_tracks = tracks
            self._log(f"Получено {len(tracks)} треков")
            if not tracks:
                messagebox.showinfo("Пусто", "Топ не получен")
                return
            PreviewWindow(
                self,
                tracks,
                on_confirm=self._add_selected_to_library,
                title=f"Топ ({self._time_var.get()})",
                confirm_text="Добавить в библиотеку",
            )

        self._run_async(fetch, on_done=on_done)

    def _quick_add_to_library(self) -> None:
        if not self._check_ready():
            return
        inputs = self._get_inputs()
        if not inputs:
            return
        time_range, limit = inputs

        self._log(f"Быстрое добавление топ-{limit}...")
        client = self._client
        assert client is not None

        def task():
            tracks = client.get_top_tracks(time_range, limit)
            track_ids = [t.id for t in tracks]
            return client.add_to_library(track_ids, progress=self._update_progress)

        self._run_async(task, on_done=self._on_add_done)

    def _add_selected_to_library(self, track_ids: list[str]) -> None:
        if not self._check_ready():
            return
        client = self._client
        assert client is not None

        self._log(f"Добавляю {len(track_ids)} выбранных треков в библиотеку...")

        def task():
            return client.add_to_library(track_ids, progress=self._update_progress)

        self._run_async(task, on_done=self._on_add_done)

    def _on_add_done(self, result: OperationResult) -> None:
        self._log(
            f"Готово! Добавлено: {result.added}, "
            f"уже было: {result.skipped}, ошибок: {result.errors}"
        )

    # ---------- плейлисты ----------

    def _refresh_playlists_async(self) -> None:
        if not self._client:
            return
        client = self._client

        def task():
            return client.get_user_playlists()

        def on_done(playlists: list[dict]):
            self._playlists_cache = playlists
            if not playlists:
                self._playlist_menu.configure(
                    values=["— нет редактируемых плейлистов —"]
                )
                self._playlist_var.set("— нет редактируемых плейлистов —")
                return
            labels = [f"{p['name']} ({p['tracks_total']})" for p in playlists]
            self._playlist_menu.configure(values=labels)
            current = self._playlist_var.get()
            if current not in labels:
                self._playlist_var.set(labels[0])
            self._log(f"Загружено плейлистов: {len(playlists)}")

        self._run_async(task, on_done=on_done)

    def _selected_playlist_id(self) -> str | None:
        label = self._playlist_var.get()
        for p in self._playlists_cache:
            if f"{p['name']} ({p['tracks_total']})" == label:
                return p["id"]
        return None

    def _create_playlist_dialog(self) -> None:
        if not self._check_ready():
            return
        client = self._client
        assert client is not None

        dlg = ctk.CTkInputDialog(
            text="Название нового плейлиста:", title="Новый плейлист"
        )
        name = dlg.get_input()
        if not name or not name.strip():
            return

        clean_name = name.strip()

        def task():
            return client.create_playlist(
                clean_name,
                public=False,
                description="Создан Spotify Library Manager",
            )

        def on_done(playlist):
            self._log(f"Создан плейлист: {playlist['name']}")
            self._refresh_playlists_async()

        self._run_async(task, on_done=on_done)

    def _add_top_to_playlist(self) -> None:
        if not self._check_ready():
            return
        client = self._client
        assert client is not None

        playlist_id = self._selected_playlist_id()
        if not playlist_id:
            messagebox.showwarning("Плейлист", "Выбери плейлист")
            return
        inputs = self._get_inputs()
        if not inputs:
            return
        time_range, limit = inputs

        playlist_label = self._playlist_var.get()
        self._log(f"Загружаю топ для плейлиста '{playlist_label}'...")

        def fetch():
            return client.get_top_tracks(time_range, limit)

        def on_fetched(tracks: list[Track]):
            if not tracks:
                self._log("Топ пуст")
                return

            def on_confirm(selected_ids: list[str]):
                def add_task():
                    return client.add_to_playlist(
                        playlist_id,
                        selected_ids,
                        skip_duplicates=True,
                        progress=self._update_progress,
                    )

                def on_added(result: OperationResult):
                    self._log(
                        f"В плейлист добавлено: {result.added}, "
                        f"пропущено дублей: {result.skipped}, ошибок: {result.errors}"
                    )
                    self._refresh_playlists_async()

                self._run_async(add_task, on_done=on_added)

            PreviewWindow(
                self,
                tracks,
                on_confirm=on_confirm,
                title=f"Добавление в '{playlist_label}'",
                confirm_text="Добавить в плейлист",
            )

        self._run_async(fetch, on_done=on_fetched)

    # ---------- очистка библиотеки ----------

    def _clear_library_dialog(self) -> None:
        if not self._check_ready():
            return
        client = self._client
        assert client is not None

        if not messagebox.askyesno(
            "ВНИМАНИЕ",
            "Удалить ВСЕ треки из библиотеки?\nЭто необратимо.",
            icon="warning",
        ):
            return

        dlg = ctk.CTkInputDialog(
            text='Введи "DELETE" чтобы подтвердить полное удаление:',
            title="Последнее подтверждение",
        )
        confirmation = dlg.get_input()
        if confirmation != "DELETE":
            self._log("Очистка отменена")
            return

        self._log("Очищаю библиотеку...")

        def task():
            return client.clear_library(progress=self._update_progress)

        def on_done(result: OperationResult):
            self._log(
                f"Библиотека очищена. Удалено: {result.removed}, ошибок: {result.errors}"
            )

        self._run_async(task, on_done=on_done)

    # ---------- лог ----------

    def _export_log(self) -> None:
        content = self._log_text.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("Пусто", "Лог пуст")
            return
        default_name = f"spotify_log_{datetime.now():%Y%m%d_%H%M%S}.txt"
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(content, encoding="utf-8")
        self._log(f"Лог сохранён: {path}")

    def _clear_log(self) -> None:
        self._log_text.delete("1.0", "end")

    # ---------- настройки ----------

    def _load_settings(self) -> None:
        if not SETTINGS_FILE.exists():
            return
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if "time" in data and data["time"] in self.TIME_RANGES:
                self._time_var.set(data["time"])
            if "limit" in data:
                self._limit_var.set(str(data["limit"]))
        except Exception as exc:
            logging.warning("Не удалось загрузить настройки: %s", exc)

    def _save_settings(self) -> None:
        try:
            data = {
                "time": self._time_var.get(),
                "limit": self._limit_var.get(),
            }
            SETTINGS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logging.warning("Не удалось сохранить настройки: %s", exc)

    def destroy(self) -> None:
        self._save_settings()
        super().destroy()


if __name__ == "__main__":
    app = SpotifyManagerApp()
    app.mainloop()
