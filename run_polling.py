r"""
run_polling.py — Local development launcher in Telegram polling mode.

Runs the bot without a public webhook URL. Telegram events are fetched
via long-polling. Alchemy on-chain webhooks won't fire in this mode (they
need a public HTTPS endpoint), but all bot commands work perfectly for
local testing.

Usage:
    .venv\Scripts\python run_polling.py
"""
from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
load_dotenv()  # Load .env before importing anything that reads settings

# On Windows the production default (/data/aster_tracker.db) doesn't exist.
# Override to a local file for polling-mode dev if DATABASE_URL isn't set.
import os
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./aster_tracker_dev.db"

from aiogram import Bot, Dispatcher

from app.config import get_settings
from app.bot import bot_router
from app.db import init_db
from app.db.base import Base

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()

    logger.info("Initialising database (auto-creating tables for local dev)...")
    init_db(settings.database_url)

    # Auto-create tables — safe for local SQLite; on Railway use Alembic instead
    from app.db.base import _engine
    from sqlalchemy.ext.asyncio import AsyncEngine
    async with _engine.begin() as conn:  # type: ignore[union-attr]
        await conn.run_sync(Base.metadata.create_all)

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(bot_router)

    # Delete any existing webhook so polling works cleanly
    await bot.delete_webhook(drop_pending_updates=True)

    logger.info("Bot started in POLLING mode. Send /start in Telegram!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
