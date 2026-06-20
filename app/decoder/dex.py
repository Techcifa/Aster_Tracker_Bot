"""
app/decoder/dex.py — Detect token buy events on Uniswap V2 and V3 DEXes.

A "token buy" is defined as: the tracked wallet receives an ERC-20 token output
in a swap, paying ETH or WETH as input in the same transaction.

Strategy:
 1. Find V2 Swap logs where `to == tracked_address`.
 2. Find V3 Swap logs where `recipient == tracked_address`.
 3. For each hit, also scan ERC-20 Transfer logs in the receipt to identify
    which token the wallet received and how much ETH/WETH was spent.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from web3 import Web3
from web3.types import TxReceipt

logger = logging.getLogger(__name__)

_ABIS_DIR = Path(__file__).parent.parent / "abis"

WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2".lower()

# keccak256("Transfer(address,address,uint256)")
ERC20_TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)

# Uniswap V2 Swap topic
UNISWAP_V2_SWAP_TOPIC = (
    "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
)
# Uniswap V3 Swap topic
UNISWAP_V3_SWAP_TOPIC = (
    "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
)

_W3 = Web3()  # ABI-only, no connection


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


def _decode_address_topic(raw: Any) -> str:
    t_hex = _topic_to_hex(raw)
    return "0x" + t_hex[-40:]


@dataclass
class TokenBuyEvent:
    token_address: str    # ERC-20 contract received
    token_symbol: str     # placeholder — enriched later if needed
    token_amount: int     # raw amount in token's smallest unit
    eth_spent_wei: int    # ETH or WETH spent
    dex: str              # "Uniswap V2" | "Uniswap V3" | "DEX"
    log_index: int        # log_index of the Swap event


def _find_erc20_received(
    receipt: TxReceipt, tracked_address: str
) -> list[tuple[str, int]]:
    """
    Return a list of (token_address, amount) for all ERC-20 tokens transferred
    TO tracked_address in this transaction.
    """
    addr_lower = tracked_address.lower()
    received: list[tuple[str, int]] = []
    for log in receipt.get("logs", []):
        if _topic0(log) != ERC20_TRANSFER_TOPIC:
            continue
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue
        to_addr = _decode_address_topic(topics[2])
        if to_addr.lower() != addr_lower:
            continue
        data = log.get("data", "0x")
        amount = int(data, 16) if data not in ("0x", "") else 0
        received.append((log["address"].lower(), amount))
    return received


def _eth_value(receipt: TxReceipt) -> int:
    """Return transaction ETH value in wei (from receipt if available)."""
    # The ETH value is on the transaction itself, not the receipt.
    # We fall back to 0 here; the Alchemy webhook payload includes value.
    return 0


def find_token_buys(
    receipt: TxReceipt, tracked_address: str, tx_eth_value: int = 0
) -> list[TokenBuyEvent]:
    """
    Identify token buy events where *tracked_address* is the swap recipient.
    """
    addr_lower = tracked_address.lower()
    results: list[TokenBuyEvent] = []

    # Collect all ERC-20 tokens received by tracked_address in this tx
    received_tokens = _find_erc20_received(receipt, tracked_address)
    if not received_tokens:
        return results  # No tokens received → not a buy

    # Try V2 Swap logs
    v2_abi = _load_abi("uniswap_v2_pair.json")
    v2_contract_template = _W3.eth.contract(
        address="0x0000000000000000000000000000000000000001", abi=v2_abi
    )

    for log in receipt.get("logs", []):
        if _topic0(log) != UNISWAP_V2_SWAP_TOPIC:
            continue
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue
        to_addr = _decode_address_topic(topics[2])
        if to_addr.lower() != addr_lower:
            continue

        # Decode amounts from data
        data = log.get("data", "0x")[2:]  # strip 0x
        if len(data) < 256:
            continue
        amount0_in  = int(data[0:64],   16)
        amount1_in  = int(data[64:128], 16)
        amount0_out = int(data[128:192], 16)
        amount1_out = int(data[192:256], 16)

        eth_spent = max(amount0_in, amount1_in)  # simplified
        if received_tokens:
            token_addr, token_amount = received_tokens[0]
            results.append(
                TokenBuyEvent(
                    token_address=token_addr,
                    token_symbol="",
                    token_amount=token_amount,
                    eth_spent_wei=eth_spent or tx_eth_value,
                    dex="Uniswap V2",
                    log_index=log["logIndex"],
                )
            )

    # Try V3 Swap logs
    for log in receipt.get("logs", []):
        if _topic0(log) != UNISWAP_V3_SWAP_TOPIC:
            continue
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue
        recipient = _decode_address_topic(topics[2])
        if recipient.lower() != addr_lower:
            continue

        data = log.get("data", "0x")[2:]
        if len(data) < 128:
            continue
        # amount0 and amount1 are int256 (signed)
        amount0_raw = int(data[0:64], 16)
        amount1_raw = int(data[64:128], 16)
        # Treat negative amounts as "out" (received)
        def to_signed(n: int) -> int:
            if n >= 2**255:
                return n - 2**256
            return n

        amount0 = to_signed(amount0_raw)
        amount1 = to_signed(amount1_raw)
        eth_spent = abs(amount0) if amount0 < 0 else abs(amount1)

        if received_tokens:
            token_addr, token_amount = received_tokens[0]
            results.append(
                TokenBuyEvent(
                    token_address=token_addr,
                    token_symbol="",
                    token_amount=token_amount,
                    eth_spent_wei=eth_spent or tx_eth_value,
                    dex="Uniswap V3",
                    log_index=log["logIndex"],
                )
            )

    return results
