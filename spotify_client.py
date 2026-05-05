"""Высокоуровневый клиент Spotify с retry, пагинацией и батчами."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Iterator

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from config import Config

logger = logging.getLogger(__name__)

# Spotify API разрешает максимум 50 ID за раз для большинства endpoint'ов
BATCH_SIZE = 20
MAX_RETRIES = 5
PROGRESS_CALLBACK = Callable[[int, int, str], None]  # (done, total, message)


@dataclass
class Track:
    """Упрощённое представление трека для UI."""

    id: str
    uri: str
    name: str
    artists: str
    album: str
    duration_ms: int

    @property
    def duration_str(self) -> str:
        seconds = self.duration_ms // 1000
        return f"{seconds // 60}:{seconds % 60:02d}"

    @classmethod
    def from_api(cls, data: dict) -> "Track":
        return cls(
            id=data["id"],
            uri=data["uri"],
            name=data["name"],
            artists=", ".join(a["name"] for a in data["artists"]),
            album=data["album"]["name"],
            duration_ms=data["duration_ms"],
        )


@dataclass
class OperationResult:
    """Результат массовой операции для статистики."""

    added: int = 0
    skipped: int = 0
    removed: int = 0
    errors: int = 0


class SpotifyClient:
    """Тонкая обёртка над spotipy с автоматическими retry и batching."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=config.client_id,
                client_secret=config.client_secret,
                redirect_uri=config.redirect_uri,
                scope=config.scope,
                cache_path=config.cache_path,
                open_browser=True,
            ),
            requests_timeout=30,
        )
        # Кешируем ID юзера — он нужен для плейлистов
        self._user_id: str | None = None

    # ---------- internal helpers ----------

    def _retry(self, func: Callable, *args, **kwargs):
        """Выполнить вызов API с авто-retry на 429 и временные 5xx."""
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except SpotifyException as e:
                if e.http_status == 429:
                    # Spotify возвращает Retry-After в секундах
                    retry_after = (
                        int(e.headers.get("Retry-After", 1)) if e.headers else 1
                    )
                    wait = min(retry_after, 30)
                    logger.warning(
                        "Rate limit hit, sleeping %ss (attempt %s)", wait, attempt + 1
                    )
                    time.sleep(wait)
                    continue
                if 500 <= (e.http_status or 0) < 600:
                    wait = 2**attempt
                    logger.warning(
                        "Server error %s, retrying in %ss", e.http_status, wait
                    )
                    time.sleep(wait)
                    continue
                raise
        raise RuntimeError(f"Превышено максимальное число попыток ({MAX_RETRIES})")

    @staticmethod
    def _chunks(items: list, size: int = BATCH_SIZE) -> Iterator[list]:
        for i in range(0, len(items), size):
            yield items[i : i + size]

    # ---------- auth / user ----------

    def me(self) -> dict:
        return self._retry(self._sp.current_user)

    def user_id(self) -> str:
        if self._user_id is None:
            self._user_id = self.me()["id"]
        return self._user_id

    # ---------- top tracks ----------

    def get_top_tracks(self, time_range: str, limit: int) -> list[Track]:
        """
        Получить топ-треки. Spotify API ограничивает limit=50 за один вызов,
        поэтому если нужно больше — делаем несколько запросов с offset.
        """
        if limit <= 0:
            return []

        all_tracks: list[Track] = []
        remaining = limit
        offset = 0

        while remaining > 0:
            batch_limit = min(remaining, 50)
            data = self._retry(
                self._sp.current_user_top_tracks,
                limit=batch_limit,
                offset=offset,
                time_range=time_range,
            )
            items = data.get("items", [])
            if not items:
                break
            all_tracks.extend(Track.from_api(t) for t in items)
            offset += len(items)
            remaining -= len(items)
            # Если Spotify вернул меньше чем просили — больше нет
            if len(items) < batch_limit:
                break

        return all_tracks

    # ---------- library ----------

    def saved_contains(self, track_ids: list[str]) -> dict[str, bool]:
        """Проверить наличие треков в библиотеке. Возвращает {id: bool}."""
        result: dict[str, bool] = {}
        # /me/library/contains использует query-string, длина URL ограничена ~2KB.
        # 50 ID = ~2200 символов → Spotify возвращает 400. Используем 20.
        for chunk in self._chunks(track_ids, size=20):
            statuses = self._retry(self._sp.current_user_saved_tracks_contains, chunk)
            for tid, status in zip(chunk, statuses):
                result[tid] = status
        return result

    def add_to_library(
        self,
        track_ids: list[str],
        progress: PROGRESS_CALLBACK | None = None,
    ) -> OperationResult:
        """Добавить треки в библиотеку, пропуская уже сохранённые."""
        result = OperationResult()
        if not track_ids:
            return result

        # Сначала проверяем что уже в библиотеке
        if progress:
            progress(0, len(track_ids), "Проверка дублей...")
        saved_map = self.saved_contains(track_ids)

        to_add = [tid for tid in track_ids if not saved_map.get(tid, False)]
        result.skipped = len(track_ids) - len(to_add)

        if not to_add:
            if progress:
                progress(len(track_ids), len(track_ids), "Все треки уже в библиотеке")
            return result

        done = 0
        for chunk in self._chunks(to_add):
            try:
                self._retry(self._sp.current_user_saved_tracks_add, chunk)
                result.added += len(chunk)
            except SpotifyException as e:
                logger.error("Failed to add chunk: %s", e)
                result.errors += len(chunk)
            done += len(chunk)
            if progress:
                progress(done, len(to_add), f"Добавлено {done}/{len(to_add)}")

        return result

    def iter_saved_tracks(self) -> Iterator[Track]:
        """Итерация по всем сохранённым трекам с пагинацией."""
        offset = 0
        while True:
            data = self._retry(
                self._sp.current_user_saved_tracks, limit=50, offset=offset
            )
            items = data.get("items", [])
            if not items:
                break
            for item in items:
                track = item.get("track")
                if track and track.get("id"):
                    yield Track.from_api(track)
            if len(items) < 50:
                break
            offset += len(items)

    def get_saved_total(self) -> int:
        """Общее количество треков в библиотеке."""
        data = self._retry(self._sp.current_user_saved_tracks, limit=1)
        return data.get("total", 0)

    def clear_library(
        self, progress: PROGRESS_CALLBACK | None = None
    ) -> OperationResult:
        """Удалить все треки из библиотеки."""
        result = OperationResult()
        total = self.get_saved_total()

        if total == 0:
            if progress:
                progress(0, 0, "Библиотека уже пуста")
            return result

        if progress:
            progress(0, total, f"Найдено {total} треков, начинаю удаление...")

        # ВАЖНО: всегда берём с offset=0, потому что после удаления
        # позиции сдвигаются. Если делать offset += 50, можно пропустить.
        while True:
            data = self._retry(self._sp.current_user_saved_tracks, limit=50, offset=0)
            items = data.get("items", [])
            if not items:
                break

            track_ids = [item["track"]["id"] for item in items if item.get("track")]
            if not track_ids:
                break

            try:
                self._retry(self._sp.current_user_saved_tracks_delete, track_ids)
                result.removed += len(track_ids)
            except SpotifyException as e:
                logger.error("Failed to delete chunk: %s", e)
                result.errors += len(track_ids)
                # Если упали — выходим чтобы не зациклиться
                break

            if progress:
                progress(result.removed, total, f"Удалено {result.removed}/{total}")

        return result

    # ---------- playlists ----------

    def get_user_playlists(self) -> list[dict]:
        """Список плейлистов пользователя (только те, что он может редактировать)."""
        playlists: list[dict] = []
        offset = 0
        my_id = self.user_id()
        while True:
            data = self._retry(self._sp.current_user_playlists, limit=50, offset=offset)
            items = data.get("items", [])
            if not items:
                break
            for p in items:
                # Можем редактировать: либо свой плейлист, либо collaborative
                if p["owner"]["id"] == my_id or p.get("collaborative"):
                    playlists.append(
                        {
                            "id": p["id"],
                            "name": p["name"],
                            "tracks_total": p["tracks"]["total"],
                        }
                    )
            if len(items) < 50:
                break
            offset += len(items)
        return playlists

    def create_playlist(
        self, name: str, public: bool = False, description: str = ""
    ) -> dict:
        playlist = self._retry(
            self._sp.user_playlist_create,
            user=self.user_id(),
            name=name,
            public=public,
            description=description,
        )
        return {"id": playlist["id"], "name": playlist["name"]}

    def get_playlist_track_ids(self, playlist_id: str) -> set[str]:
        """ID всех треков в плейлисте — для проверки дублей."""
        ids: set[str] = set()
        offset = 0
        while True:
            data = self._retry(
                self._sp.playlist_items,
                playlist_id,
                fields="items(track(id)),total",
                limit=100,
                offset=offset,
                additional_types=("track",),
            )
            items = data.get("items", [])
            if not items:
                break
            for item in items:
                track = item.get("track")
                if track and track.get("id"):
                    ids.add(track["id"])
            if len(items) < 100:
                break
            offset += len(items)
        return ids

    def add_to_playlist(
        self,
        playlist_id: str,
        track_ids: list[str],
        skip_duplicates: bool = True,
        progress: PROGRESS_CALLBACK | None = None,
    ) -> OperationResult:
        result = OperationResult()
        if not track_ids:
            return result

        if skip_duplicates:
            if progress:
                progress(0, len(track_ids), "Проверка дублей в плейлисте...")
            existing = self.get_playlist_track_ids(playlist_id)
            to_add = [t for t in track_ids if t not in existing]
            result.skipped = len(track_ids) - len(to_add)
        else:
            to_add = list(track_ids)

        if not to_add:
            if progress:
                progress(len(track_ids), len(track_ids), "Все треки уже в плейлисте")
            return result

        # playlist_add_items принимает максимум 100 за раз
        done = 0
        for chunk in self._chunks(to_add, size=100):
            try:
                uris = [f"spotify:track:{tid}" for tid in chunk]
                self._retry(self._sp.playlist_add_items, playlist_id, uris)
                result.added += len(chunk)
            except SpotifyException as e:
                logger.error("Failed to add to playlist: %s", e)
                result.errors += len(chunk)
            done += len(chunk)
            if progress:
                progress(done, len(to_add), f"Добавлено {done}/{len(to_add)}")

        return result
