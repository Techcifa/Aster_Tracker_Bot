"""
app/notifier/queue.py — Rate-limited Telegram message delivery.

Architecture:
  - A single asyncio.Queue holds (chat_id, text) tuples.
  - A single worker coroutine drains the queue, respecting Telegram's flood
    limit of ~1 message/second per chat (30 msg/s globally).
  - The worker applies per-chat token bucket rate limiting.

Usage:
    from app.notifier.queue import enqueue_message, start_notifier_worker
    task = asyncio.create_task(start_notifier_worker(bot))
    ...
    await enqueue_message(chat_id, text)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

logger = logging.getLogger(__name__)

_queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()

# Per-chat rate limiter state: {chat_id: last_send_timestamp}
_last_sent: dict[int, float] = defaultdict(float)
_MIN_INTERVAL_SEC = 1.1  # slightly above Telegram's 1 msg/s per chat limit


async def enqueue_message(chat_id: int, text: str) -> None:
    """Add a message to the delivery queue (non-blocking)."""
    await _queue.put((chat_id, text))


async def start_notifier_worker(bot: Bot) -> None:
    """
    Long-running coroutine that processes queued Telegram messages.
    Should be run as an asyncio.Task during the application lifetime.
    """
    logger.info("Notifier worker started.")
    while True:
        chat_id, text = await _queue.get()
        try:
            # Per-chat rate limiting
            now = time.monotonic()
            elapsed = now - _last_sent[chat_id]
            if elapsed < _MIN_INTERVAL_SEC:
                await asyncio.sleep(_MIN_INTERVAL_SEC - elapsed)

            await _send_with_retry(bot, chat_id, text)
            _last_sent[chat_id] = time.monotonic()

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("Notifier worker error for chat %d: %s", chat_id, e)
        finally:
            _queue.task_done()


async def _send_with_retry(
    bot: Bot,
    chat_id: int,
    text: str,
    max_retries: int = 3,
) -> None:
    """Send a message, retrying on TelegramRetryAfter (flood control) errors."""
    for attempt in range(max_retries):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        except TelegramRetryAfter as e:
            wait = e.retry_after + 1
            logger.warning(
                "Telegram flood limit hit for chat %d; waiting %ds", chat_id, wait
            )
            await asyncio.sleep(wait)
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error(
                    "Failed to send message to chat %d after %d attempts: %s",
                    chat_id, max_retries, e,
                )
            else:
                await asyncio.sleep(2**attempt)
