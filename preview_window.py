"""Окно предпросмотра треков с чекбоксами."""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from spotify_client import Track


class PreviewWindow(ctk.CTkToplevel):
    """
    Модальное окно со списком треков и чекбоксами.
    После закрытия вызывает on_confirm(selected_track_ids) если юзер подтвердил,
    или ничего если отменил.
    """

    def __init__(
        self,
        master,
        tracks: list[Track],
        on_confirm: Callable[[list[str]], None],
        title: str = "Предпросмотр треков",
        confirm_text: str = "Добавить выбранные",
    ) -> None:
        super().__init__(master)
        self.title(title)
        self.geometry("780x600")
        self.transient(master)
        self.grab_set()

        self._tracks = tracks
        self._on_confirm = on_confirm
        self._vars: dict[str, ctk.BooleanVar] = {}

        self._build_ui(confirm_text)

    def _build_ui(self, confirm_text: str) -> None:
        # Заголовок + счётчик
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(20, 10))

        self._counter_label = ctk.CTkLabel(
            header,
            text=f"Выбрано: {len(self._tracks)} из {len(self._tracks)}",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self._counter_label.pack(side="left")

        ctk.CTkButton(
            header, text="Снять все", width=100, command=self._deselect_all
        ).pack(side="right", padx=5)
        ctk.CTkButton(
            header, text="Выбрать все", width=100, command=self._select_all
        ).pack(side="right", padx=5)

        # Скроллируемый список
        scroll = ctk.CTkScrollableFrame(self, label_text="")
        scroll.pack(fill="both", expand=True, padx=20, pady=10)

        for idx, track in enumerate(self._tracks, start=1):
            var = ctk.BooleanVar(value=True)
            var.trace_add("write", lambda *_: self._update_counter())
            self._vars[track.id] = var

            row = ctk.CTkFrame(scroll, fg_color=("gray85", "gray20"), corner_radius=6)
            row.pack(fill="x", pady=2, padx=2)

            cb = ctk.CTkCheckBox(row, text="", variable=var, width=24)
            cb.pack(side="left", padx=(10, 5), pady=8)

            num = ctk.CTkLabel(
                row,
                text=f"{idx:>3}.",
                width=35,
                font=ctk.CTkFont(size=12),
                text_color=("gray40", "gray60"),
            )
            num.pack(side="left")

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=5, pady=4)

            ctk.CTkLabel(
                info,
                text=track.name,
                font=ctk.CTkFont(size=13, weight="bold"),
                anchor="w",
            ).pack(fill="x")
            ctk.CTkLabel(
                info,
                text=f"{track.artists}  ·  {track.album}",
                font=ctk.CTkFont(size=11),
                text_color=("gray30", "gray70"),
                anchor="w",
            ).pack(fill="x")

            ctk.CTkLabel(
                row,
                text=track.duration_str,
                font=ctk.CTkFont(size=11),
                text_color=("gray40", "gray60"),
                width=50,
            ).pack(side="right", padx=10)

        # Кнопки действий
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=20, pady=(10, 20))

        ctk.CTkButton(
            actions,
            text="Отмена",
            fg_color="gray",
            hover_color="darkgray",
            command=self.destroy,
        ).pack(side="left", expand=True, fill="x", padx=(0, 5))

        ctk.CTkButton(
            actions,
            text=confirm_text,
            fg_color="#1DB954",
            hover_color="#1AA34A",
            command=self._confirm,
        ).pack(side="left", expand=True, fill="x", padx=(5, 0))

    def _select_all(self) -> None:
        for var in self._vars.values():
            var.set(True)

    def _deselect_all(self) -> None:
        for var in self._vars.values():
            var.set(False)

    def _update_counter(self) -> None:
        selected = sum(1 for v in self._vars.values() if v.get())
        self._counter_label.configure(
            text=f"Выбрано: {selected} из {len(self._tracks)}"
        )

    def _confirm(self) -> None:
        selected = [tid for tid, var in self._vars.items() if var.get()]
        self.destroy()
        if selected:
            self._on_confirm(selected)
