"""
app/poller/opensea_stream.py — OpenSea Stream WebSocket client.

Connects to OpenSea's Phoenix-Channels WebSocket endpoint and subscribes to
the global `collection:*` channel for `item_listed` events. Events are filtered
locally against the set of actively-tracked wallet addresses.

Protocol notes:
  - OpenSea uses Phoenix Channels (Elixir) over WebSocket.
  - Messages are JSON arrays: [join_ref, ref, topic, event, payload]
  - Heartbeat must be sent every 30 seconds or the server disconnects.

WebSocket URL:
  wss://stream.openseabeta.com/socket/websocket?token=<API_KEY>

This module exports:
  run_opensea_stream(api_key, get_tracked_addrs, on_listing)

Where:
  get_tracked_addrs() → Callable returning set of lowercase addresses
  on_listing(wallet, event_payload) → async callback

Reference: https://docs.opensea.io/reference/stream-api-overview
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable, Set

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

_STREAM_URL = "wss://stream.openseabeta.com/socket/websocket"
_HEARTBEAT_INTERVAL = 25  # seconds (server timeout is 30s)
_RECONNECT_DELAY_MIN = 5   # seconds
_RECONNECT_DELAY_MAX = 120  # seconds

# Phoenix Channels join/event constants
_TOPIC_ALL_COLLECTIONS = "collection:*"
_EVENT_ITEM_LISTED = "item_listed"
_EVENT_PHX_JOIN = "phx_join"
_EVENT_PHX_REPLY = "phx_reply"
_EVENT_PHX_HEARTBEAT = "heartbeat"
_PHOENIX_TOPIC = "phoenix"


def _build_join_msg(ref: int) -> str:
    """Build a Phoenix Channels join message for collection:*"""
    return json.dumps([None, str(ref), _TOPIC_ALL_COLLECTIONS, _EVENT_PHX_JOIN, {}])


def _build_heartbeat_msg(ref: int) -> str:
    return json.dumps([None, str(ref), _PHOENIX_TOPIC, _EVENT_PHX_HEARTBEAT, {}])


async def _heartbeat_loop(ws: websockets.WebSocketClientProtocol) -> None:
    """Send periodic heartbeats to keep the connection alive."""
    ref = 1000
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
        try:
            await ws.send(_build_heartbeat_msg(ref))
            ref += 1
        except Exception:
            break  # Connection closed, loop exits naturally


async def run_opensea_stream(
    api_key: str,
    get_tracked_addrs: Callable[[], Awaitable[Set[str]]],
    on_listing: Callable[[str, dict], Awaitable[None]],
) -> None:
    """
    Persistent WebSocket client — reconnects automatically on disconnects.

    :param api_key: OpenSea API key
    :param get_tracked_addrs: Async callable returning set of lowercase wallet
                               addresses currently being tracked (called on
                               each event to filter relevantly).
    :param on_listing: Async callback invoked with (wallet_address, payload)
                       when a new listing is detected for a tracked wallet.
    """
    url = f"{_STREAM_URL}?token={api_key}&vsn=2.0.0"
    delay = _RECONNECT_DELAY_MIN

    while True:
        # ref_counter is reset each connection cycle so it never grows unbounded
        ref_counter = 1
        heartbeat_task: asyncio.Task | None = None
        logger.info("Connecting to OpenSea Stream WebSocket...")

        try:
            async with websockets.connect(
                url,
                ping_interval=None,   # We manage heartbeats manually
                ping_timeout=None,
                max_size=2**20,       # 1 MB max message size
                open_timeout=30,
            ) as ws:
                logger.info("OpenSea Stream connected.")
                delay = _RECONNECT_DELAY_MIN  # Reset backoff on successful connect

                # Join the global collection channel
                await ws.send(_build_join_msg(ref_counter))
                ref_counter += 1

                # Start heartbeat — guaranteed to be cancelled in the finally below
                heartbeat_task = asyncio.create_task(_heartbeat_loop(ws))

                try:
                    async for raw_message in ws:
                        await _handle_message(
                            raw_message, get_tracked_addrs, on_listing
                        )
                finally:
                    # Always cancel the heartbeat when the message loop exits
                    # (normal close, exception, or CancelledError)
                    if heartbeat_task and not heartbeat_task.done():
                        heartbeat_task.cancel()
                    if heartbeat_task:
                        try:
                            await heartbeat_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        heartbeat_task = None

        except asyncio.CancelledError:
            logger.info("OpenSea Stream task cancelled — shutting down.")
            # Ensure heartbeat cleaned up even if CancelledError fires
            # before heartbeat_task is assigned or its finally runs
            if heartbeat_task and not heartbeat_task.done():
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except (asyncio.CancelledError, Exception):
                    pass
            return
        except ConnectionClosed as e:
            logger.warning("OpenSea Stream disconnected: %s. Reconnecting in %ds...", e, delay)
        except Exception as e:
            logger.exception("OpenSea Stream error: %s. Reconnecting in %ds...", e, delay)

        await asyncio.sleep(delay)
        delay = min(delay * 2, _RECONNECT_DELAY_MAX)



async def _handle_message(
    raw: str | bytes,
    get_tracked_addrs: Callable[[], Awaitable[Set[str]]],
    on_listing: Callable[[str, dict], Awaitable[None]],
) -> None:
    """Parse a Phoenix Channels message and dispatch item_listed events."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return

    # Phoenix message format: [join_ref, ref, topic, event, payload]
    if not isinstance(msg, list) or len(msg) < 5:
        return

    _join_ref, _ref, topic, event, payload = msg[0], msg[1], msg[2], msg[3], msg[4]

    # Ignore heartbeat replies and join confirmations
    if event in (_EVENT_PHX_REPLY, _EVENT_PHX_HEARTBEAT):
        return

    if event != _EVENT_ITEM_LISTED:
        return

    # Extract maker address from payload
    # OpenSea payload structure: {event_type, payload: {maker: {address: "0x..."}}}
    try:
        inner_payload = payload.get("payload", payload)
        maker_address = inner_payload.get("maker", {}).get("address", "").lower()
        if not maker_address:
            return

        # Check if this maker is one of our tracked wallets
        tracked = await get_tracked_addrs()
        if maker_address not in tracked:
            return

        logger.info("OpenSea listing detected: maker=%s", maker_address)
        await on_listing(maker_address, inner_payload)

    except Exception as e:
        logger.warning("Error processing OpenSea Stream message: %s", e)
