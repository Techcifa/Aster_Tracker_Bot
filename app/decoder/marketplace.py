"""
app/decoder/marketplace.py — Decode NFT buy / sell events from major marketplaces.

Supported:
  - Seaport 1.5  (OpenSea, Blur listings settled on Seaport)
  - Blur Exchange V1
  - LooksRare V2
  - X2Y2

For each recognised fill event in *receipt*, returns a MarketplaceEvent
classifying the tracked_address as buyer or seller.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from web3 import Web3
from web3.types import TxReceipt

logger = logging.getLogger(__name__)

_ABIS_DIR = Path(__file__).parent.parent / "abis"

# ── Contract addresses (checksummed) ──────────────────────────────────────────
SEAPORT_1_5    = "0x00000000000000ADc04C56Bf30aC9d3c0aAF14dC"
BLUR_EXCHANGE  = "0x000000000000Ad05Ccc4F10045630fb830B95127"
LOOKSRARE_V2   = "0x0000000000E655fAe4d56241588680F86E3b2377"
X2Y2_EXCHANGE  = "0x74312363e45DCaBA76c59ec49a13Aa114147427"

# ── Known event topic hashes ──────────────────────────────────────────────────
# Computed offline: Web3.keccak(text="EventSignature(...)").hex()
TOPIC_SEAPORT_ORDER_FULFILLED = (
    "0x9d9af8e38d66c62e2c12f0225249fd9d721c54b83f48d9352c97c6cacdcb6f31"
)
TOPIC_BLUR_ORDERS_MATCHED = (
    "0x61cbb2a3dee0b6064c2e681aadd61677fb4ef319f0b547508d495626f5a62f64"
)
TOPIC_LOOKSRARE_TAKER_ASK = (
    "0x9aaa45d6db2ef74ead0751ea9113263d1dec1b50cea05f0ca2002cb8063564a4"
)
TOPIC_LOOKSRARE_TAKER_BID = (
    "0x3ee3de4684413690dee6fff1a0a4f92916a1b97d1c5a83cdf24671844306b2e3"
)
TOPIC_X2Y2_EV_INVENTORY = (
    "0x3cbb63f144840e5b1b0a38a7c19211d2e89de4d7c5faf8b2d3c1776c302d1d33"
)

WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
ETH_ITEM_TYPE = 0  # Seaport ItemType.NATIVE
WETH_ITEM_TYPE = 1  # Seaport ItemType.ERC20 (could be any ERC20, check token)
NFT_ITEM_TYPES = {2, 3, 4, 5}  # ERC721, ERC1155, ERC721_WITH_CRITERIA, ERC1155_WITH_CRITERIA

_W3 = Web3()  # Used only for ABI utilities — no connection needed


def _load_abi(name: str) -> list[dict]:
    return json.loads((_ABIS_DIR / name).read_text())


def _topic_to_hex(topic: Any) -> str:
    """Ensure topic is a lowercase hex string starting with 0x."""
    if isinstance(topic, bytes):
        return "0x" + topic.hex()
    t_str = str(topic).lower()
    if not t_str.startswith("0x"):
        return "0x" + t_str
    return t_str


def _topic0(log: dict) -> str:
    topics = log.get("topics", [])
    if not topics:
        return ""
    return _topic_to_hex(topics[0])


@dataclass
class MarketplaceEvent:
    event_type: str        # "buy_nft" or "sell_nft"
    collection: str        # NFT contract address
    token_id: str          # NFT token ID
    price_wei: int         # price in wei
    counterparty: str      # the other party in the trade
    marketplace: str       # "OpenSea" | "Blur" | "LooksRare" | "X2Y2"
    log_index: int
    order_hash: str = ""


# ── Seaport 1.5 ───────────────────────────────────────────────────────────────

def _decode_seaport(receipt: TxReceipt, tracked: str) -> list[MarketplaceEvent]:
    """Decode Seaport 1.5 OrderFulfilled events."""
    abi = _load_abi("seaport_1_5.json")
    contract = _W3.eth.contract(
        address=Web3.to_checksum_address(SEAPORT_1_5), abi=abi
    )
    events: list[MarketplaceEvent] = []
    try:
        decoded_logs = contract.events.OrderFulfilled().process_receipt(
            receipt, errors=contract.events.OrderFulfilled.DISCARD
        )
    except Exception as e:
        logger.debug("Seaport decode error: %s", e)
        return events

    tracked_lower = tracked.lower()

    for log in decoded_logs:
        args = log["args"]
        offerer = args["offerer"].lower()
        recipient = args["recipient"].lower()
        offer = args["offer"]
        consideration = args["consideration"]
        order_hash = args["orderHash"].hex()
        log_index = log["logIndex"]

        # Determine NFT token and price
        nft_item = next(
            (i for i in offer if i["itemType"] in NFT_ITEM_TYPES), None
        )
        if nft_item is None:
            # NFT might be in consideration (offer = payment, consideration = NFT)
            nft_item = next(
                (i for i in consideration if i["itemType"] in NFT_ITEM_TYPES), None
            )

        if nft_item is None:
            continue

        # Price = sum of ETH/WETH consideration items
        price_wei = sum(
            i["amount"]
            for i in consideration
            if i["itemType"] in (ETH_ITEM_TYPE, WETH_ITEM_TYPE)
        )
        if price_wei == 0:
            price_wei = sum(
                i["amount"]
                for i in offer
                if i["itemType"] in (ETH_ITEM_TYPE, WETH_ITEM_TYPE)
            )

        collection = nft_item["token"]
        token_id = str(nft_item["identifier"])

        if offerer == tracked_lower:
            event_type = "sell_nft"
            counterparty = recipient
        elif recipient == tracked_lower:
            event_type = "buy_nft"
            counterparty = offerer
        else:
            continue

        events.append(
            MarketplaceEvent(
                event_type=event_type,
                collection=collection,
                token_id=token_id,
                price_wei=price_wei,
                counterparty=counterparty,
                marketplace="OpenSea",
                log_index=log_index,
                order_hash=order_hash,
            )
        )
    return events


# ── Blur Exchange V1 ──────────────────────────────────────────────────────────

def _decode_blur(receipt: TxReceipt, tracked: str) -> list[MarketplaceEvent]:
    """Decode Blur Exchange V1 OrdersMatched events."""
    abi = _load_abi("blur_exchange.json")
    contract = _W3.eth.contract(
        address=Web3.to_checksum_address(BLUR_EXCHANGE), abi=abi
    )
    events: list[MarketplaceEvent] = []
    try:
        decoded_logs = contract.events.OrdersMatched().process_receipt(
            receipt, errors=contract.events.OrdersMatched.DISCARD
        )
    except Exception as e:
        logger.debug("Blur decode error: %s", e)
        return events

    tracked_lower = tracked.lower()

    for log in decoded_logs:
        args = log["args"]
        maker = args["maker"].lower()
        taker = args["taker"].lower()
        sell_order = args["sell"]
        log_index = log["logIndex"]

        collection = sell_order["collection"].lower()
        token_id = str(sell_order["tokenId"])
        price_wei = sell_order["price"]
        sell_hash = args["sellHash"].hex()

        if maker == tracked_lower:
            event_type = "sell_nft"
            counterparty = taker
        elif taker == tracked_lower:
            event_type = "buy_nft"
            counterparty = maker
        else:
            continue

        events.append(
            MarketplaceEvent(
                event_type=event_type,
                collection=collection,
                token_id=token_id,
                price_wei=price_wei,
                counterparty=counterparty,
                marketplace="Blur",
                log_index=log_index,
                order_hash=sell_hash,
            )
        )
    return events


# ── LooksRare V2 ─────────────────────────────────────────────────────────────

def _decode_looksrare(receipt: TxReceipt, tracked: str) -> list[MarketplaceEvent]:
    """Decode LooksRare V2 TakerAsk / TakerBid events."""
    abi = _load_abi("looksrare.json")
    contract = _W3.eth.contract(
        address=Web3.to_checksum_address(LOOKSRARE_V2), abi=abi
    )
    events: list[MarketplaceEvent] = []
    tracked_lower = tracked.lower()

    for event_name, side in [("TakerAsk", "ask"), ("TakerBid", "bid")]:
        try:
            event_obj = getattr(contract.events, event_name)()
            decoded_logs = event_obj.process_receipt(
                receipt, errors=event_obj.DISCARD
            )
        except Exception as e:
            logger.debug("LooksRare %s decode error: %s", event_name, e)
            continue

        for log in decoded_logs:
            args = log["args"]
            ask_user = args.get("askUser", "").lower()
            bid_user = args.get("bidUser", "").lower()
            collection = args["collection"].lower()
            item_ids = args["itemIds"]
            fee_amounts = args["feeAmounts"]
            log_index = log["logIndex"]
            order_hash = args["nonceInvalidationParameters"]["orderHash"].hex()

            price_wei = fee_amounts[0] if fee_amounts else 0
            token_id = str(item_ids[0]) if item_ids else "0"

            if side == "ask" and ask_user == tracked_lower:
                events.append(
                    MarketplaceEvent(
                        event_type="sell_nft",
                        collection=collection,
                        token_id=token_id,
                        price_wei=price_wei,
                        counterparty=bid_user,
                        marketplace="LooksRare",
                        log_index=log_index,
                        order_hash=order_hash,
                    )
                )
            elif side == "bid" and bid_user == tracked_lower:
                events.append(
                    MarketplaceEvent(
                        event_type="buy_nft",
                        collection=collection,
                        token_id=token_id,
                        price_wei=price_wei,
                        counterparty=ask_user,
                        marketplace="LooksRare",
                        log_index=log_index,
                        order_hash=order_hash,
                    )
                )

    return events


# ── X2Y2 ─────────────────────────────────────────────────────────────────────

def _decode_x2y2(receipt: TxReceipt, tracked: str) -> list[MarketplaceEvent]:
    """Decode X2Y2 EvInventory events."""
    abi = _load_abi("x2y2.json")
    contract = _W3.eth.contract(
        address=Web3.to_checksum_address(X2Y2_EXCHANGE), abi=abi
    )
    events: list[MarketplaceEvent] = []
    try:
        decoded_logs = contract.events.EvInventory().process_receipt(
            receipt, errors=contract.events.EvInventory.DISCARD
        )
    except Exception as e:
        logger.debug("X2Y2 decode error: %s", e)
        return events

    tracked_lower = tracked.lower()

    for log in decoded_logs:
        args = log["args"]
        maker = args["maker"].lower()
        taker = args["taker"].lower()
        item = args["item"]
        log_index = log["logIndex"]
        item_hash = args["itemHash"].hex()

        price_wei = item["price"]
        # X2Y2 encodes collection/tokenId in item.data (ABI-encoded)
        # Simple heuristic: parse first 64 bytes as (address, tokenId)
        data: bytes = item["data"]
        collection = "0x" + data[12:32].hex() if len(data) >= 32 else "0x"
        token_id = str(int.from_bytes(data[32:64], "big")) if len(data) >= 64 else "0"

        if maker == tracked_lower:
            event_type = "sell_nft"
            counterparty = taker
        elif taker == tracked_lower:
            event_type = "buy_nft"
            counterparty = maker
        else:
            continue

        events.append(
            MarketplaceEvent(
                event_type=event_type,
                collection=collection,
                token_id=token_id,
                price_wei=price_wei,
                counterparty=counterparty,
                marketplace="X2Y2",
                log_index=log_index,
                order_hash=item_hash,
            )
        )
    return events


# ── Public interface ──────────────────────────────────────────────────────────

def find_marketplace_events(
    receipt: TxReceipt, tracked_address: str
) -> list[MarketplaceEvent]:
    """
    Try all supported marketplaces; return any buy/sell events involving
    *tracked_address*.
    """
    results: list[MarketplaceEvent] = []
    # Fast early rejection: check if any relevant topic appears in logs
    all_topics = set()
    for log in receipt.get("logs", []):
        for t in log.get("topics", []):
            all_topics.add(t.hex() if isinstance(t, bytes) else t)

    if TOPIC_SEAPORT_ORDER_FULFILLED in all_topics:
        results.extend(_decode_seaport(receipt, tracked_address))
    if TOPIC_BLUR_ORDERS_MATCHED in all_topics:
        results.extend(_decode_blur(receipt, tracked_address))
    if TOPIC_LOOKSRARE_TAKER_ASK in all_topics or TOPIC_LOOKSRARE_TAKER_BID in all_topics:
        results.extend(_decode_looksrare(receipt, tracked_address))
    if TOPIC_X2Y2_EV_INVENTORY in all_topics:
        results.extend(_decode_x2y2(receipt, tracked_address))

    return results
