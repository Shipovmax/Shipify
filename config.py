"""Загрузка и валидация конфигурации из .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    client_id: str
    client_secret: str
    redirect_uri: str
    cache_path: str
    scope: str = "user-top-read user-library-read user-library-modify playlist-modify-private playlist-modify-public playlist-read-private"

    @classmethod
    def load(cls) -> "Config":
        # Ищем .env рядом с исполняемым файлом
        env_path = Path(__file__).parent / ".env"
        load_dotenv(env_path)

        client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
        client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
        redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/callback").strip()
        cache_path = os.getenv("SPOTIFY_CACHE_PATH", ".spotify_cache").strip()

        if not client_id or client_id == "your_client_id_here":
            raise ConfigError(
                "SPOTIFY_CLIENT_ID не задан. Скопируй .env.example в .env и заполни."
            )
        if not client_secret or client_secret == "your_client_secret_here":
            raise ConfigError(
                "SPOTIFY_CLIENT_SECRET не задан. Скопируй .env.example в .env и заполни."
            )

        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            cache_path=cache_path,
        )


class ConfigError(Exception):
    """Ошибка конфигурации."""
