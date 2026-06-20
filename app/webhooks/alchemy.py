"""
app/webhooks/alchemy.py — Webhook endpoint for receiving Alchemy Address Activity.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from web3 import Web3

from app.config import get_settings
from app.db import crud, get_session
from app.decoder.classifier import classify_transaction
from app.notifier import enqueue_message, format_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhooks"])


def validate_signature(body: bytes, header_sig: str | None, signing_key: str) -> None:
    """Validate that the incoming request signature matches the signing key."""
    if not header_sig:
        raise HTTPException(status_code=401, detail="Missing signature header")
    computed = hmac.new(signing_key.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, header_sig):
        raise HTTPException(status_code=403, detail="Invalid signature")


async def process_alchemy_payload(payload: dict) -> None:
    """
    Background task to parse and classify on-chain transactions from the Alchemy webhook.
    """
    settings = get_settings()
    w3 = Web3(Web3.HTTPProvider(settings.alchemy_rpc_url))
    
    event_data = payload.get("event", {})
    activities = event_data.get("activity", [])
    if not activities:
        return
        
    # Get all active tracked addresses from db
    async with get_session() as session:
        tracked_addresses = set(await crud.get_all_tracked_addresses(session))
        
    if not tracked_addresses:
        return
        
    # Find unique (tx_hash, tracked_address) pairs in the payload
    to_process = set()
    for act in activities:
        tx_hash = act.get("hash")
        if not tx_hash:
            continue
        for key in ("fromAddress", "toAddress"):
            addr_str = act.get(key)
            if not addr_str:
                continue
            try:
                chk = Web3.to_checksum_address(addr_str)
                if chk in tracked_addresses:
                    to_process.add((tx_hash, chk))
            except ValueError:
                continue

    # Process each pair
    for tx_hash, tracked_address in to_process:
        try:
            logger.info("Processing activity for tx %s and address %s", tx_hash, tracked_address)
            events = classify_transaction(w3, tx_hash, tracked_address)
            if not events:
                continue
                
            async with get_session() as session:
                # Retrieve label once to avoid lazy loading issues
                wallet_obj = await crud.get_wallet_by_address(session, tracked_address)
                wallet_label = wallet_obj.label if wallet_obj else None
                
                for event in events:
                    # Save seen events for idempotency
                    is_new = await crud.try_insert_seen_event(session, event.event_key, event.event_type)
                    if not is_new:
                        logger.info("Skipping duplicate event: %s", event.event_key)
                        continue
                        
                    # Find matching subscriptions
                    subs = await crud.get_subscribers_for_wallet(session, event.wallet_address)
                    for user, sub in subs:
                        # Check event filter toggles
                        if event.event_type == "mint" and not sub.notify_mint:
                            continue
                        if event.event_type == "buy_nft" and not sub.notify_buy_nft:
                            continue
                        if event.event_type == "sell_nft" and not sub.notify_sell_nft:
                            continue
                        if event.event_type == "token_buy" and not sub.notify_token_buy:
                            continue
                            
                        # Check min value filter
                        value_eth = 0.0
                        if event.event_type in ("buy_nft", "sell_nft"):
                            value_eth = event.price_wei / 1e18
                        elif event.event_type == "token_buy":
                            value_eth = event.eth_spent_wei / 1e18
                        elif event.event_type == "mint":
                            value_eth = event.mint_cost_wei / 1e18
                            
                        if value_eth < float(sub.min_value_eth):
                            logger.info(
                                "Muting event %s for chat %d due to min_value_eth threshold (%.4f ETH < %.4f ETH)",
                                event.event_key, user.telegram_chat_id, value_eth, float(sub.min_value_eth)
                            )
                            continue
                            
                        # Format event and enqueue telegram message
                        msg = format_event(event, wallet_label)
                        if msg:
                            await enqueue_message(user.telegram_chat_id, msg)
                await session.commit()
        except Exception as e:
            logger.exception("Error processing transaction %s for address %s: %s", tx_hash, tracked_address, e)


@router.post("/alchemy")
async def alchemy_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Webhook receiver endpoint. Validates signature and enqueues processing in background tasks.
    """
    settings = get_settings()
    body = await request.body()
    
    # Verify signature
    sig = request.headers.get("x-alchemy-signature")
    validate_signature(body, sig, settings.alchemy_webhook_signing_key)
    
    # Process payload in background
    try:
        payload = json.loads(body)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
        
    background_tasks.add_task(process_alchemy_payload, payload)
    return {"status": "ok"}
