"""
app/notifier/formats.py — Message formatters for each event type.

All formatters return a Markdown-formatted string ready to send via Telegram
(parse_mode=MarkdownV2 compatible — we use HTML mode here for simplicity).
"""
from __future__ import annotations

from web3 import Web3

from app.decoder.classifier import NormalizedEvent

_WEI = 10**18


def _fmt_addr(addr: str, label: str | None = None) -> str:
    """Short address display: 0x1234...abcd (Label)"""
    short = f"{addr[:6]}...{addr[-4:]}"
    return f"{short} ({label})" if label else short


def _fmt_eth(wei: int, decimals: int = 4) -> str:
    if wei == 0:
        return "?"
    eth = wei / _WEI
    return f"{eth:.{decimals}f} ETH"


def _fmt_token_amount(amount: int, decimals: int = 18) -> str:
    """Format a raw ERC-20 amount with assumed 18 decimals (most tokens)."""
    if amount == 0:
        return "?"
    val = amount / (10**decimals)
    if val >= 1_000_000:
        return f"{val/1_000_000:.2f}M"
    if val >= 1_000:
        return f"{val/1_000:.2f}K"
    return f"{val:.4f}"


def _etherscan_tx(tx_hash: str) -> str:
    return f"https://etherscan.io/tx/{tx_hash}"


def _etherscan_addr(addr: str) -> str:
    return f"https://etherscan.io/address/{addr}"


def fmt_mint(event: NormalizedEvent, label: str | None = None) -> str:
    wallet = _fmt_addr(event.wallet_address, label)
    cost = _fmt_eth(event.mint_cost_wei)
    token_line = (
        f"Token #{event.token_id}"
        if event.token_id and event.token_id != "batch"
        else "Token (batch)"
    )
    collection_short = _fmt_addr(event.collection)
    return (
        f"🎨 <b>MINT</b>\n"
        f"Wallet: <code>{wallet}</code>\n"
        f"Collection: <code>{collection_short}</code>\n"
        f"{token_line}\n"
        f"Cost: {cost}\n"
        f"🔗 <a href=\"{_etherscan_tx(event.tx_hash)}\">etherscan.io/tx/{event.tx_hash[:10]}...</a>"
    )


def fmt_token_buy(event: NormalizedEvent, label: str | None = None) -> str:
    wallet = _fmt_addr(event.wallet_address, label)
    amount = _fmt_token_amount(event.token_amount)
    token_short = _fmt_addr(event.token_address)
    symbol = event.token_symbol or token_short
    spent = _fmt_eth(event.eth_spent_wei)
    dex = event.dex or "DEX"
    return (
        f"💰 <b>TOKEN BUY</b>\n"
        f"Wallet: <code>{wallet}</code>\n"
        f"Bought: {amount} {symbol}\n"
        f"Spent: {spent}\n"
        f"DEX: {dex}\n"
        f"🔗 <a href=\"{_etherscan_tx(event.tx_hash)}\">etherscan.io/tx/{event.tx_hash[:10]}...</a>"
    )


def fmt_nft_buy(event: NormalizedEvent, label: str | None = None) -> str:
    wallet = _fmt_addr(event.wallet_address, label)
    price = _fmt_eth(event.price_wei)
    collection_short = _fmt_addr(event.collection)
    marketplace = event.marketplace or "Unknown"
    return (
        f"🛒 <b>NFT BUY</b>\n"
        f"Wallet: <code>{wallet}</code>\n"
        f"Collection: <code>{collection_short}</code> #{event.token_id}\n"
        f"Price: {price}\n"
        f"Marketplace: {marketplace}\n"
        f"🔗 <a href=\"{_etherscan_tx(event.tx_hash)}\">etherscan.io/tx/{event.tx_hash[:10]}...</a>"
    )


def fmt_nft_sell(event: NormalizedEvent, label: str | None = None) -> str:
    wallet = _fmt_addr(event.wallet_address, label)
    price = _fmt_eth(event.price_wei)
    collection_short = _fmt_addr(event.collection)
    marketplace = event.marketplace or "Unknown"
    return (
        f"✅ <b>NFT SOLD</b>\n"
        f"Wallet: <code>{wallet}</code>\n"
        f"Collection: <code>{collection_short}</code> #{event.token_id}\n"
        f"Sold for: {price}\n"
        f"Marketplace: {marketplace}\n"
        f"🔗 <a href=\"{_etherscan_tx(event.tx_hash)}\">etherscan.io/tx/{event.tx_hash[:10]}...</a>"
    )


def fmt_nft_list(
    wallet_address: str,
    collection: str,
    token_id: str,
    price_wei: int,
    marketplace: str,
    order_hash: str,
    label: str | None = None,
) -> str:
    wallet = _fmt_addr(wallet_address, label)
    price = _fmt_eth(price_wei)
    collection_short = _fmt_addr(collection)
    # Build OpenSea URL from order hash where possible
    opensea_url = f"https://opensea.io/assets/ethereum/{collection}/{token_id}"
    return (
        f"🏷️ <b>NFT LISTED</b>\n"
        f"Wallet: <code>{wallet}</code>\n"
        f"Collection: <code>{collection_short}</code> #{token_id}\n"
        f"Listed at: {price}\n"
        f"Marketplace: {marketplace}\n"
        f"🔗 <a href=\"{opensea_url}\">opensea.io</a>"
    )


def format_event(
    event: NormalizedEvent, label: str | None = None
) -> str | None:
    """Route an event to the correct formatter. Returns None if unknown type."""
    if event.event_type == "mint":
        return fmt_mint(event, label)
    if event.event_type == "token_buy":
        return fmt_token_buy(event, label)
    if event.event_type == "buy_nft":
        return fmt_nft_buy(event, label)
    if event.event_type == "sell_nft":
        return fmt_nft_sell(event, label)
    return None
