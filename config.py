"""Loading and validation of configuration from .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    """Immutable application configuration loaded from environment variables.

    Attributes:
        client_id: Spotify application client ID.
        client_secret: Spotify application client secret.
        redirect_uri: OAuth redirect URI registered with the Spotify app.
        cache_path: Filesystem path where the OAuth token cache is stored.
        scope: Space-separated list of Spotify OAuth scopes requested.
    """

    client_id: str
    client_secret: str
    redirect_uri: str
    cache_path: str
    scope: str = "user-top-read user-library-read user-library-modify playlist-modify-private playlist-modify-public playlist-read-private"

    @classmethod
    def load(cls) -> "Config":
        """Load configuration from a `.env` file next to this module.

        Returns:
            A populated, validated `Config` instance.

        Raises:
            ConfigError: If `SPOTIFY_CLIENT_ID` or `SPOTIFY_CLIENT_SECRET`
                is missing or still set to its placeholder value.
        """
        # Look for .env next to the executable file
        env_path = Path(__file__).parent / ".env"
        load_dotenv(env_path)

        client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
        client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
        redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/callback").strip()
        cache_path = os.getenv("SPOTIFY_CACHE_PATH", ".spotify_cache").strip()

        if not client_id or client_id == "your_client_id_here":
            raise ConfigError(
                "SPOTIFY_CLIENT_ID is not set. Copy .env.example to .env and fill it in."
            )
        if not client_secret or client_secret == "your_client_secret_here":
            raise ConfigError(
                "SPOTIFY_CLIENT_SECRET is not set. Copy .env.example to .env and fill it in."
            )

        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            cache_path=cache_path,
        )


class ConfigError(Exception):
    """Raised when the application configuration is missing or invalid."""
