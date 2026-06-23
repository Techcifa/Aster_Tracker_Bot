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
from app.notifier import start_notifier_worker
from app.poller import run_opensea_stream

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
    async with _engine.begin() as conn:  # type: ignore[union-attr]
        await conn.run_sync(Base.metadata.create_all)

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(bot_router)

    # Delete any existing webhook so polling works cleanly
    await bot.delete_webhook(drop_pending_updates=True)

    # ── Background workers ────────────────────────────────────────────────────
    # Start the rate-limited notifier so Telegram messages can be dispatched
    # even during local polling mode.
    notifier_task = asyncio.create_task(start_notifier_worker(bot))
    logger.info("Notifier worker started.")

    # Start the OpenSea stream if an API key is configured (optional for local dev).
    opensea_task: asyncio.Task | None = None
    if settings.opensea_api_key and settings.opensea_api_key.strip():
        from app.main import get_tracked_addresses, handle_opensea_listing
        opensea_task = asyncio.create_task(
            run_opensea_stream(
                api_key=settings.opensea_api_key,
                get_tracked_addrs=get_tracked_addresses,
                on_listing=handle_opensea_listing,
            )
        )
        logger.info("OpenSea stream started.")
    else:
        logger.info(
            "OPENSEA_API_KEY not set — OpenSea listing stream will not run locally. "
            "Set it in .env to test listing notifications."
        )

    logger.info("Bot started in POLLING mode. Send /start in Telegram!")
    try:
        await dp.start_polling(bot)
    finally:
        # Clean up background workers on exit (Ctrl+C)
        notifier_task.cancel()
        if opensea_task:
            opensea_task.cancel()
        tasks = [t for t in [notifier_task, opensea_task] if t]
        await asyncio.gather(*tasks, return_exceptions=True)
        await bot.session.close()
        logger.info("Polling mode shut down cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
