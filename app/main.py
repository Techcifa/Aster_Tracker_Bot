"""
app/main.py — FastAPI application entry point.

Manages startup and shutdown lifecycles:
  - Database initialization/cleanup.
  - Setting up the Telegram Bot and webhook path.
  - Spawning the notifier worker background task.
  - Spawning the OpenSea Stream WebSocket consumer.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request

from app.bot import bot_router
from app.config import get_settings
from app.db import close_db, crud, get_session, init_db
from app.notifier import enqueue_message, format_event, fmt_nft_list, start_notifier_worker
from app.poller import run_opensea_stream
from app.webhooks import webhook_router

# Set up logging configuration
settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Global instances updated during lifespan
bot: Bot | None = None
dp: Dispatcher | None = None


# Set to True only after init_db() + create_all succeed.
# Guards the OpenSea stream and callbacks from running without a live DB.
_db_ready: bool = False


async def get_tracked_addresses() -> set[str]:
    """Callback for the OpenSea Stream client to fetch actively tracked addresses."""
    if not _db_ready:
        logger.warning("get_tracked_addresses called before DB is ready — returning empty set.")
        return set()
    async with get_session() as session:
        addrs = await crud.get_all_tracked_addresses(session)
    return {a.lower() for a in addrs}


async def handle_opensea_listing(maker_address: str, payload: dict) -> None:
    """Callback for the OpenSea Stream client when a listing is matched."""
    if not _db_ready:
        logger.warning("handle_opensea_listing called before DB is ready — skipping event.")
        return
    # Parse NFT identifiers
    nft_id = payload.get("item", {}).get("nft_id", "")
    collection = ""
    token_id = ""
    if nft_id:
        parts = nft_id.split("/")
        if len(parts) >= 3:
            collection = parts[1]
            token_id = parts[2]

    order_hash = payload.get("order_hash", "")
    price_wei = int(payload.get("base_price") or 0)
    event_key = f"list:{order_hash}"

    logger.info("Handling OpenSea listing event %s for %s", event_key, maker_address)

    async with get_session() as session:
        # Check seen events for idempotency
        is_new = await crud.try_insert_seen_event(session, event_key, "list")
        if not is_new:
            logger.info("OpenSea listing event %s already processed. Skipping.", event_key)
            return

        # Retrieve wallet label
        wallet_obj = await crud.get_wallet_by_address(session, maker_address)
        wallet_label = wallet_obj.label if wallet_obj else None

        # Process active subscriptions
        subs = await crud.get_subscribers_for_wallet(session, maker_address)
        for user, sub in subs:
            # Check toggles
            if not sub.notify_list_nft:
                continue

            # Check min value threshold
            value_eth = price_wei / 1e18
            if value_eth < float(sub.min_value_eth):
                continue

            # Format and send listing message
            msg = fmt_nft_list(
                wallet_address=maker_address,
                collection=collection,
                token_id=token_id,
                price_wei=price_wei,
                marketplace="OpenSea",
                order_hash=order_hash,
                label=wallet_label,
            )
            if msg:
                await enqueue_message(user.telegram_chat_id, msg)
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot, dp
    settings = get_settings()

    # ── Database ──────────────────────────────────────────────────────────────
    global _db_ready
    _db_ready = False
    try:
        from pathlib import Path
        db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
        if db_path.startswith("/"):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        logger.info("Initializing database...")
        init_db(settings.database_url)

        # Auto-create all tables — safe no-op if they already exist.
        logger.info("Ensuring database schema is up to date...")
        import app.db.base as _db_base
        async with _db_base._engine.begin() as conn:
            await conn.run_sync(_db_base.Base.metadata.create_all)

        _db_ready = True
        logger.info("Database ready.")
    except Exception as exc:
        logger.exception("Database initialisation failed: %s", exc)
        # Continue — healthcheck must pass; OpenSea stream will NOT start (db_ready=False).

    # ── Telegram Bot ──────────────────────────────────────────────────────────
    logger.info("Setting up Telegram Bot client...")
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(bot_router)

    # Register the slash-command menu shown in the Telegram UI
    try:
        from aiogram.types import BotCommand
        await asyncio.wait_for(
            bot.set_my_commands([
                BotCommand(command="start",   description="Welcome message & feature overview"),
                BotCommand(command="track",   description="Track a wallet — /track <address> [label]"),
                BotCommand(command="untrack", description="Stop tracking — /untrack <address>"),
                BotCommand(command="list",    description="List all your tracked wallets"),
                BotCommand(command="filters", description="Configure alerts — /filters <address>"),
                BotCommand(command="help",    description="Show command reference"),
            ]),
            timeout=10.0
        )
        logger.info("Bot commands registered.")
    except asyncio.TimeoutError:
        logger.warning("Timed out registering bot commands (non-fatal)")
    except Exception as exc:
        logger.warning("Could not register bot commands (non-fatal): %s", exc)

    # Register Telegram webhook — only when a real HTTPS URL is configured.
    # On the first Railway deploy WEBHOOK_BASE_URL may not be set yet;
    # the app must still start so the /health check passes.
    webhook_url = settings.telegram_webhook_url
    if webhook_url.startswith("https://"):
        try:
            logger.info("Registering Telegram webhook at %s", webhook_url)
            await asyncio.wait_for(
                bot.set_webhook(
                    url=webhook_url,
                    secret_token=settings.telegram_webhook_secret,
                ),
                timeout=10.0
            )
            logger.info("Telegram webhook registered successfully.")
        except asyncio.TimeoutError:
            logger.warning("Timed out registering Telegram webhook (non-fatal)")
        except Exception as exc:
            logger.warning("Telegram webhook registration failed (non-fatal): %s", exc)
    else:
        logger.warning(
            "WEBHOOK_BASE_URL is not an HTTPS URL ('%s'). "
            "Telegram webhook NOT registered — set WEBHOOK_BASE_URL in Railway Variables "
            "and redeploy to activate incoming updates.",
            settings.webhook_base_url,
        )

    # ── Background workers ────────────────────────────────────────────────────
    # Start the rate-limited message worker
    notifier_task = asyncio.create_task(start_notifier_worker(bot))

    # Start the OpenSea Stream client only when DB is confirmed ready.
    opensea_task = None
    if not _db_ready:
        logger.warning(
            "OpenSea stream will NOT start because the database is not ready. "
            "Fix DB initialisation errors and redeploy."
        )
    elif settings.opensea_api_key and settings.opensea_api_key.strip():
        logger.info("Starting OpenSea stream background listener...")
        opensea_task = asyncio.create_task(
            run_opensea_stream(
                api_key=settings.opensea_api_key,
                get_tracked_addrs=get_tracked_addresses,
                on_listing=handle_opensea_listing,
            )
        )
    else:
        logger.warning(
            "OPENSEA_API_KEY is empty. OpenSea live listings stream will not start."
        )

    yield


    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Shutting down workers and connections...")

    if notifier_task:
        notifier_task.cancel()
    if opensea_task:
        opensea_task.cancel()

    tasks = [t for t in [notifier_task, opensea_task] if t]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    if bot:
        await bot.session.close()

    await close_db()
    logger.info("Shutdown sequence complete.")


app = FastAPI(lifespan=lifespan, title="Aster Tracker API")

# Register Webhook Router (Alchemy API)
app.include_router(webhook_router)


@app.post(settings.telegram_webhook_path)
async def telegram_webhook(request: Request):
    """
    Webserver endpoint to receive updates pushed from Telegram.
    """
    global bot, dp
    if not bot or not dp:
        raise HTTPException(status_code=503, detail="Telegram services not ready")

    # Validate secret token header to ensure authenticity
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    # Feed updates into dispatch pipeline
    try:
        update_json = await request.json()
        update = Update.model_validate(update_json, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception as e:
        logger.exception("Error processing Telegram update: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "ok"}


@app.get("/health")
async def health_check():
    """Simple API health check endpoint."""
    return {"status": "healthy"}
