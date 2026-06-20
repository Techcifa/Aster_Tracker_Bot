"""
app/config.py — Centralised settings using pydantic-settings.

All values are read from environment variables (or a .env file when running
locally). Pydantic validates types and raises a clear error if a required
variable is missing.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def resolve_webhook_base_url(self) -> Settings:
        import os
        public_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
        static_url = os.environ.get("RAILWAY_STATIC_URL")
        # Prefer static_url if it starts with https://, otherwise use public_domain
        resolved = None
        if static_url and static_url.startswith("https://") and not ".internal" in static_url:
            resolved = static_url
        elif public_domain:
            resolved = f"https://{public_domain}"

        if resolved:
            is_internal = ".internal" in self.webhook_base_url
            is_local = "localhost" in self.webhook_base_url or "127.0.0.1" in self.webhook_base_url
            if not self.webhook_base_url.startswith("https://") or is_internal or is_local:
                self.webhook_base_url = resolved
        return self

    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_bot_token: str
    telegram_webhook_secret: str = "local-dev-secret"
    webhook_base_url: str = "http://localhost:8000"  # overridden on Railway

    # ── Alchemy ───────────────────────────────────────────────────────────────
    alchemy_api_key: str = ""
    alchemy_webhook_signing_key: str = "local-dev-signing-key"
    alchemy_webhook_id: str = ""
    alchemy_auth_token: str = ""
    alchemy_network: str = "mainnet"

    # ── OpenSea ───────────────────────────────────────────────────────────────
    opensea_api_key: str = ""

    # ── Database ──────────────────────────────────────────────────────────────
    # On Railway: mount a Persistent Volume at /data — the DB lives there.
    # Locally:    falls back to a sqlite file next to the project root.
    database_url: str = "sqlite+aiosqlite:////data/aster_tracker.db"

    # ── App ───────────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Computed helpers ──────────────────────────────────────────────────────
    @property
    def alchemy_rpc_url(self) -> str:
        return f"https://eth-{self.alchemy_network}.g.alchemy.com/v2/{self.alchemy_api_key}"

    @property
    def telegram_webhook_path(self) -> str:
        return f"/webhook/telegram/{self.telegram_webhook_secret}"

    @property
    def telegram_webhook_url(self) -> str:
        return f"{self.webhook_base_url}{self.telegram_webhook_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
