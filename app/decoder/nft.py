"""
app/decoder/nft.py — Detect ERC-721 / ERC-1155 mint events.

ERC-721  Transfer: from==address(0) + to==tracked_wallet → mint
ERC-1155 TransferSingle / TransferBatch: from==address(0) → mint
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from eth_typing import HexStr
from web3.types import TxReceipt

logger = logging.getLogger(__name__)

# keccak256("Transfer(address,address,uint256)")
ERC721_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# keccak256("TransferSingle(address,address,address,uint256,uint256)")
ERC1155_TRANSFER_SINGLE_TOPIC = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"

# keccak256("TransferBatch(address,address,address,uint256[],uint256[])")
ERC1155_TRANSFER_BATCH_TOPIC = "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


@dataclass
class MintEvent:
    collection: str   # contract address
    token_id: str     # token ID as string (may be list for batch)
    amount: int       # quantity (always 1 for ERC-721)
    standard: str     # "ERC-721" or "ERC-1155"
    log_index: int

def _topic_to_hex(topic: Any) -> str:
    """Ensure topic is a lowercase hex string starting with 0x."""
    if isinstance(topic, bytes):
        return "0x" + topic.hex()
    t_str = str(topic).lower()
    if not t_str.startswith("0x"):
        return "0x" + t_str
    return t_str


def _decode_address(topic: Any) -> str:
    """Extract a padded address from a 32-byte topic."""
    t_hex = _topic_to_hex(topic)
    return "0x" + t_hex[-40:]


def find_mints(receipt: TxReceipt, tracked_address: str) -> list[MintEvent]:
    """
    Scan all logs in *receipt* and return MintEvent objects for any NFT mint
    where the recipient is *tracked_address*.
    """
    addr_lower = tracked_address.lower()
    mints: list[MintEvent] = []

    for log in receipt["logs"]:
        topics = log.get("topics", [])
        if not topics:
            continue

        topic0 = _topic_to_hex(topics[0])
        log_index = log["logIndex"]

        # ── ERC-721 Transfer ─────────────────────────────────────────────────
        if topic0 == ERC721_TRANSFER_TOPIC and len(topics) >= 3:
            from_addr = _decode_address(topics[1])
            to_addr = _decode_address(topics[2])
            if from_addr == ZERO_ADDRESS and to_addr.lower() == addr_lower:
                # Token ID is the 4th topic (ERC-721 indexed) or in data
                if len(topics) >= 4:
                    token_id = int(_topic_to_hex(topics[3]), 16)
                else:
                    data = log.get("data", "0x")
                    token_id = int(data, 16) if data not in ("0x", "") else 0
                mints.append(
                    MintEvent(
                        collection=log["address"],
                        token_id=str(token_id),
                        amount=1,
                        standard="ERC-721",
                        log_index=log_index,
                    )
                )

        # ── ERC-1155 TransferSingle ───────────────────────────────────────────
        elif topic0 == ERC1155_TRANSFER_SINGLE_TOPIC and len(topics) >= 4:
            from_addr = _decode_address(topics[2])
            to_addr = _decode_address(topics[3])
            if from_addr == ZERO_ADDRESS and to_addr.lower() == addr_lower:
                data = log.get("data", "0x")
                if len(data) >= 130:  # 2 + 64 + 64
                    token_id = int(data[2:66], 16)
                    amount = int(data[66:130], 16)
                else:
                    token_id, amount = 0, 1
                mints.append(
                    MintEvent(
                        collection=log["address"],
                        token_id=str(token_id),
                        amount=amount,
                        standard="ERC-1155",
                        log_index=log_index,
                    )
                )

        # ── ERC-1155 TransferBatch ────────────────────────────────────────────
        elif topic0 == ERC1155_TRANSFER_BATCH_TOPIC and len(topics) >= 4:
            from_addr = _decode_address(topics[2])
            to_addr = _decode_address(topics[3])
            if from_addr == ZERO_ADDRESS and to_addr.lower() == addr_lower:
                mints.append(
                    MintEvent(
                        collection=log["address"],
                        token_id="batch",
                        amount=1,
                        standard="ERC-1155",
                        log_index=log_index,
                    )
                )

    return mints
