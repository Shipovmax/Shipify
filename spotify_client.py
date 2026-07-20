"""High-level Spotify client with retry, pagination and batching."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator, TypeVar

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from config import Config

logger = logging.getLogger(__name__)

# The Spotify API allows a maximum of 50 IDs per call for most endpoints
BATCH_SIZE = 20
MAX_RETRIES = 5
PROGRESS_CALLBACK = Callable[[int, int, str], None]  # (done, total, message)

T = TypeVar("T")


@dataclass
class Track:
    """Simplified track representation used by the UI layer.

    Attributes:
        id: Spotify track ID.
        uri: Spotify track URI (e.g. `spotify:track:<id>`).
        name: Track title.
        artists: Comma-separated list of artist names.
        album: Album title.
        duration_ms: Track duration in milliseconds.
    """

    id: str
    uri: str
    name: str
    artists: str
    album: str
    duration_ms: int

    @property
    def duration_str(self) -> str:
        """Return the track duration formatted as `M:SS`."""
        seconds = self.duration_ms // 1000
        return f"{seconds // 60}:{seconds % 60:02d}"

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Track":
        """Build a `Track` from a raw Spotify Web API track object.

        Args:
            data: Raw track dict as returned by the Spotify Web API.

        Returns:
            The corresponding `Track` instance.

        Raises:
            KeyError: If a required field is missing from `data`.
        """
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
    """Aggregated outcome of a bulk operation, used for reporting to the UI."""

    added: int = 0
    skipped: int = 0
    removed: int = 0
    errors: int = 0


class SpotifyClient:
    """Thin wrapper around spotipy with automatic retry and batching."""

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
        # Cache the user ID — needed for playlist operations
        self._user_id: str | None = None

    # ---------- internal helpers ----------

    def _retry(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Call a Spotify API function with automatic retry on 429/5xx.

        Args:
            func: The bound spotipy client method to call.
            *args: Positional arguments forwarded to `func`.
            **kwargs: Keyword arguments forwarded to `func`.

        Returns:
            Whatever `func` returns on success.

        Raises:
            SpotifyException: If a non-retryable API error occurs, or a
                retryable error persists past the final attempt.
            RuntimeError: If `MAX_RETRIES` is exhausted without success.
        """
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except SpotifyException as e:
                if e.http_status == 429:
                    # Spotify returns Retry-After in seconds
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
        raise RuntimeError(f"Maximum number of retries exceeded ({MAX_RETRIES})")

    @staticmethod
    def _chunks(items: list[Any], size: int = BATCH_SIZE) -> Iterator[list[Any]]:
        """Split `items` into consecutive chunks of at most `size` elements."""
        for i in range(0, len(items), size):
            yield items[i : i + size]

    # ---------- auth / user ----------

    def me(self) -> dict[str, Any]:
        """Fetch the current authenticated user's profile.

        Returns:
            Raw Spotify user object.

        Raises:
            SpotifyException: If the API call fails after retries.
        """
        return self._retry(self._sp.current_user)

    def user_id(self) -> str:
        """Return the current user's Spotify ID, caching it after the first call.

        Returns:
            The Spotify user ID.

        Raises:
            SpotifyException: If the underlying API call fails after retries.
        """
        if self._user_id is None:
            self._user_id = self.me()["id"]
        return self._user_id

    # ---------- top tracks ----------

    def get_top_tracks(self, time_range: str, limit: int) -> list[Track]:
        """Fetch the user's top tracks for a given time range.

        The Spotify API caps `limit` at 50 per call, so if more tracks are
        requested, multiple paginated requests are issued using `offset`.

        Args:
            time_range: One of `"short_term"`, `"medium_term"`, `"long_term"`.
            limit: Total number of tracks to fetch (may exceed 50).

        Returns:
            A list of `Track` objects, possibly shorter than `limit` if
            Spotify has fewer tracks available.

        Raises:
            SpotifyException: If the API call fails after retries.
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
            # If Spotify returned fewer items than requested — there are no more
            if len(items) < batch_limit:
                break

        return all_tracks

    # ---------- library ----------

    def saved_contains(self, track_ids: list[str]) -> dict[str, bool]:
        """Check which of the given tracks are already saved in the library.

        Args:
            track_ids: Spotify track IDs to check.

        Returns:
            Mapping of track ID to whether it is saved.

        Raises:
            SpotifyException: If the API call fails after retries.
        """
        result: dict[str, bool] = {}
        # /me/library/contains uses a query string, URL length is capped at ~2KB.
        # 50 IDs = ~2200 characters -> Spotify returns 400. We use 20 instead.
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
        """Add tracks to the user's library, skipping ones already saved.

        Args:
            track_ids: Spotify track IDs to add.
            progress: Optional callback invoked as `(done, total, message)`
                to report progress.

        Returns:
            An `OperationResult` summarizing how many tracks were added,
            skipped as duplicates, or failed.
        """
        result = OperationResult()
        if not track_ids:
            return result

        # First check what's already in the library
        if progress:
            progress(0, len(track_ids), "Checking for duplicates...")
        saved_map = self.saved_contains(track_ids)

        to_add = [tid for tid in track_ids if not saved_map.get(tid, False)]
        result.skipped = len(track_ids) - len(to_add)

        if not to_add:
            if progress:
                progress(
                    len(track_ids),
                    len(track_ids),
                    "All tracks are already in the library",
                )
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
                progress(done, len(to_add), f"Added {done}/{len(to_add)}")

        return result

    def iter_saved_tracks(self) -> Iterator[Track]:
        """Iterate over all saved tracks in the library, handling pagination.

        Yields:
            `Track` objects for every saved track, in Spotify's returned order.

        Raises:
            SpotifyException: If a page request fails after retries.
        """
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
        """Return the total number of tracks saved in the library.

        Returns:
            Total saved track count.

        Raises:
            SpotifyException: If the API call fails after retries.
        """
        data = self._retry(self._sp.current_user_saved_tracks, limit=1)
        return data.get("total", 0)

    def clear_library(
        self, progress: PROGRESS_CALLBACK | None = None
    ) -> OperationResult:
        """Remove every track from the user's library.

        Args:
            progress: Optional callback invoked as `(done, total, message)`
                to report progress.

        Returns:
            An `OperationResult` summarizing how many tracks were removed
            and how many chunk deletions failed.
        """
        result = OperationResult()
        total = self.get_saved_total()

        if total == 0:
            if progress:
                progress(0, 0, "Library is already empty")
            return result

        if progress:
            progress(0, total, f"Found {total} tracks, starting deletion...")

        # IMPORTANT: always fetch with offset=0, because after deletion
        # positions shift. Using offset += 50 could skip tracks.
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
                # Stop on failure to avoid looping forever
                break

            if progress:
                progress(result.removed, total, f"Removed {result.removed}/{total}")

        return result

    # ---------- playlists ----------

    def get_user_playlists(self) -> list[dict[str, Any]]:
        """List playlists the user is able to edit.

        Returns:
            A list of dicts with keys `id`, `name`, `tracks_total`, limited
            to playlists owned by the user or marked collaborative.

        Raises:
            SpotifyException: If a page request fails after retries.
        """
        playlists: list[dict[str, Any]] = []
        offset = 0
        my_id = self.user_id()
        while True:
            data = self._retry(self._sp.current_user_playlists, limit=50, offset=offset)
            items = data.get("items", [])
            if not items:
                break
            for p in items:
                # Editable by us: either our own playlist, or collaborative
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
    ) -> dict[str, str]:
        """Create a new playlist for the current user.

        Args:
            name: Playlist name.
            public: Whether the playlist should be public.
            description: Optional playlist description.

        Returns:
            A dict with keys `id` and `name` for the created playlist.

        Raises:
            SpotifyException: If the API call fails after retries.
        """
        playlist = self._retry(
            self._sp.user_playlist_create,
            user=self.user_id(),
            name=name,
            public=public,
            description=description,
        )
        return {"id": playlist["id"], "name": playlist["name"]}

    def get_playlist_track_ids(self, playlist_id: str) -> set[str]:
        """Return the set of all track IDs currently in a playlist.

        Used to detect duplicates before adding new tracks.

        Args:
            playlist_id: Spotify playlist ID.

        Returns:
            Set of track IDs present in the playlist.

        Raises:
            SpotifyException: If a page request fails after retries.
        """
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
        """Add tracks to a playlist, optionally skipping ones already present.

        Args:
            playlist_id: Spotify playlist ID.
            track_ids: Track IDs to add.
            skip_duplicates: If `True`, tracks already in the playlist are
                skipped instead of added again.
            progress: Optional callback invoked as `(done, total, message)`
                to report progress.

        Returns:
            An `OperationResult` summarizing how many tracks were added,
            skipped as duplicates, or failed.
        """
        result = OperationResult()
        if not track_ids:
            return result

        if skip_duplicates:
            if progress:
                progress(
                    0, len(track_ids), "Checking for duplicates in the playlist..."
                )
            existing = self.get_playlist_track_ids(playlist_id)
            to_add = [t for t in track_ids if t not in existing]
            result.skipped = len(track_ids) - len(to_add)
        else:
            to_add = list(track_ids)

        if not to_add:
            if progress:
                progress(
                    len(track_ids),
                    len(track_ids),
                    "All tracks are already in the playlist",
                )
            return result

        # playlist_add_items accepts a maximum of 100 items per call
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
                progress(done, len(to_add), f"Added {done}/{len(to_add)}")

        return result
