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

    # ── Post-init resolution ──────────────────────────────────────────────────
    @model_validator(mode="after")
    def _resolve_runtime_values(self) -> Settings:
        import os

        # 1. Resolve webhook_base_url from Railway env vars when not already HTTPS
        public_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
        static_url = os.environ.get("RAILWAY_STATIC_URL", "")

        resolved_public = None
        if static_url.startswith("https://") and ".internal" not in static_url:
            resolved_public = static_url
        elif public_domain:
            resolved_public = f"https://{public_domain}"

        if resolved_public:
            needs_override = (
                not self.webhook_base_url.startswith("https://")
                or ".internal" in self.webhook_base_url
                or "localhost" in self.webhook_base_url
                or "127.0.0.1" in self.webhook_base_url
            )
            if needs_override:
                self.webhook_base_url = resolved_public

        # 2. Rewrite postgresql:// → postgresql+asyncpg:// for async compatibility
        if self.database_url.startswith("postgresql://"):
            self.database_url = self.database_url.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )

        # 3. Fall back to a writable local SQLite path if /data doesn't exist
        if self.database_url == "sqlite+aiosqlite:////data/aster_tracker.db":
            import os as _os
            if not _os.path.isdir("/data"):
                self.database_url = "sqlite+aiosqlite:///./aster_tracker_dev.db"

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
