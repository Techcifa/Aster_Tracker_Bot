"""
app/decoder/classifier.py — Orchestrates all decoders for a given transaction.

Called from the Alchemy webhook background task. Fetches the full tx receipt
from the RPC, runs the NFT / marketplace / DEX decoders, and returns a list of
NormalizedEvent objects ready for the notifier.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from web3 import Web3
from web3.types import TxReceipt

from app.decoder.dex import TokenBuyEvent, find_token_buys
from app.decoder.marketplace import MarketplaceEvent, find_marketplace_events
from app.decoder.nft import MintEvent, find_mints

logger = logging.getLogger(__name__)


@dataclass
class NormalizedEvent:
    """
    Unified event object passed to the notifier.

    event_type: "mint" | "buy_nft" | "sell_nft" | "token_buy"
    """
    event_type: str
    wallet_address: str    # tracked wallet that triggered this event
    tx_hash: str
    log_index: int
    event_key: str         # "{tx_hash}:{log_index}" for seen_events dedup

    # NFT fields (mint / buy_nft / sell_nft)
    collection: str = ""
    token_id: str = ""
    nft_standard: str = ""
    price_wei: int = 0
    marketplace: str = ""
    counterparty: str = ""
    order_hash: str = ""

    # Token swap fields
    token_address: str = ""
    token_symbol: str = ""
    token_amount: int = 0
    eth_spent_wei: int = 0
    dex: str = ""

    # Cost for mints
    mint_cost_wei: int = 0


def _fetch_receipt(w3: Web3, tx_hash: str) -> TxReceipt | None:
    try:
        return w3.eth.get_transaction_receipt(tx_hash)
    except Exception as e:
        logger.warning("Failed to fetch receipt for %s: %s", tx_hash, e)
        return None


def _fetch_tx(w3: Web3, tx_hash: str) -> Any:
    try:
        return w3.eth.get_transaction(tx_hash)
    except Exception as e:
        logger.warning("Failed to fetch transaction %s: %s", tx_hash, e)
        return None


def classify_transaction(
    w3: Web3,
    tx_hash: str,
    tracked_address: str,
) -> list[NormalizedEvent]:
    """
    Full classification pipeline for a single transaction hash.

    Returns a list of NormalizedEvent (may be empty or contain multiple events
    if e.g. a tx both mints and swaps).
    """
    receipt = _fetch_receipt(w3, tx_hash)
    if receipt is None:
        return []

    tx = _fetch_tx(w3, tx_hash)
    tx_eth_value = tx.get("value", 0) if tx else 0

    results: list[NormalizedEvent] = []

    # 1. NFT Mints
    for mint in find_mints(receipt, tracked_address):
        results.append(
            NormalizedEvent(
                event_type="mint",
                wallet_address=tracked_address,
                tx_hash=tx_hash,
                log_index=mint.log_index,
                event_key=f"{tx_hash}:{mint.log_index}",
                collection=mint.collection,
                token_id=mint.token_id,
                nft_standard=mint.standard,
                mint_cost_wei=tx_eth_value,
            )
        )

    # 2. Marketplace buy / sell
    for mp in find_marketplace_events(receipt, tracked_address):
        results.append(
            NormalizedEvent(
                event_type=mp.event_type,
                wallet_address=tracked_address,
                tx_hash=tx_hash,
                log_index=mp.log_index,
                event_key=f"{tx_hash}:{mp.log_index}",
                collection=mp.collection,
                token_id=mp.token_id,
                price_wei=mp.price_wei,
                marketplace=mp.marketplace,
                counterparty=mp.counterparty,
                order_hash=mp.order_hash,
            )
        )

    # 3. Token swaps (only if no marketplace events — avoids mislabelling
    #    NFT purchases that involve WETH as "token buy")
    mp_event_types = {e.event_type for e in results}
    if "buy_nft" not in mp_event_types and "sell_nft" not in mp_event_types:
        for swap in find_token_buys(receipt, tracked_address, tx_eth_value):
            results.append(
                NormalizedEvent(
                    event_type="token_buy",
                    wallet_address=tracked_address,
                    tx_hash=tx_hash,
                    log_index=swap.log_index,
                    event_key=f"{tx_hash}:{swap.log_index}",
                    token_address=swap.token_address,
                    token_symbol=swap.token_symbol,
                    token_amount=swap.token_amount,
                    eth_spent_wei=swap.eth_spent_wei,
                    dex=swap.dex,
                )
            )

    return results
