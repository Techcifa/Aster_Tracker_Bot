"""
tests/test_decoder.py — Tests for the NFT and DEX swap event decoders.
"""
from __future__ import annotations

from app.decoder.nft import find_mints
from app.decoder.dex import find_token_buys


def test_find_mints_erc721():
    """Verify that ERC-721 mint logs are correctly detected."""
    tracked_address = "0x1111111111111111111111111111111111111111"
    
    # Construct a mock ERC-721 Transfer log from zero address to tracked address
    mock_log = {
        "address": "0xAbc1234567890123456789012345678901234567",
        "topics": [
            bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"),  # Transfer topic
            bytes.fromhex("0000000000000000000000000000000000000000000000000000000000000000"),  # From zero address
            bytes.fromhex("0000000000000000000000001111111111111111111111111111111111111111"),  # To tracked
            bytes.fromhex("000000000000000000000000000000000000000000000000000000000000007b")   # Token ID 123
        ],
        "data": "0x",
        "logIndex": 42
    }
    mock_receipt = {"logs": [mock_log]}

    mints = find_mints(mock_receipt, tracked_address)
    
    assert len(mints) == 1
    assert mints[0].collection == mock_log["address"]
    assert mints[0].token_id == "123"
    assert mints[0].amount == 1
    assert mints[0].standard == "ERC-721"
    assert mints[0].log_index == 42


def test_find_mints_erc721_ignored():
    """Verify that transfers from non-zero addresses are ignored by the mint detector."""
    tracked_address = "0x1111111111111111111111111111111111111111"
    
    # Transfer from non-zero address to tracked address
    mock_log = {
        "address": "0xAbc1234567890123456789012345678901234567",
        "topics": [
            bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"),
            bytes.fromhex("0000000000000000000000002222222222222222222222222222222222222222"),  # Non-zero sender
            bytes.fromhex("0000000000000000000000001111111111111111111111111111111111111111"),
            bytes.fromhex("000000000000000000000000000000000000000000000000000000000000007b")
        ],
        "data": "0x",
        "logIndex": 42
    }
    mock_receipt = {"logs": [mock_log]}

    mints = find_mints(mock_receipt, tracked_address)
    assert len(mints) == 0


def test_find_token_buys_uniswap_v2():
    """Verify Uniswap V2 token buy logic."""
    tracked_address = "0x1111111111111111111111111111111111111111"
    
    # 1. ERC-20 Transfer to the tracked address (representing received tokens)
    transfer_log = {
        "address": "0xTokenAddress000000000000000000000000000",
        "topics": [
            bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"),  # Transfer topic
            bytes.fromhex("0000000000000000000000009999999999999999999999999999999999999999"),  # From pool
            bytes.fromhex("0000000000000000000000001111111111111111111111111111111111111111")   # To tracked
        ],
        "data": "0x" + (1000 * 10**18).to_bytes(32, "big").hex(),  # 1000 tokens
        "logIndex": 1
    }
    
    # 2. V2 Swap log (to tracked address)
    # data: amount0In, amount1In, amount0Out, amount1Out (each 32 bytes)
    # Let's say amount0In = 0.5 ETH (spent), amount1Out = 1000 tokens (received)
    amount0_in = (5 * 10**17).to_bytes(32, "big").hex()  # 0.5 ETH input
    amount1_in = (0).to_bytes(32, "big").hex()
    amount0_out = (0).to_bytes(32, "big").hex()
    amount1_out = (1000 * 10**18).to_bytes(32, "big").hex()
    
    swap_log = {
        "address": "0xUniswapV2Pair00000000000000000000000000",
        "topics": [
            bytes.fromhex("d78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"),  # Swap topic
            bytes.fromhex("0000000000000000000000008888888888888888888888888888888888888888"),  # Sender
            bytes.fromhex("0000000000000000000000001111111111111111111111111111111111111111")   # To tracked
        ],
        "data": "0x" + amount0_in + amount1_in + amount0_out + amount1_out,
        "logIndex": 2
    }
    
    mock_receipt = {"logs": [transfer_log, swap_log]}
    
    swaps = find_token_buys(mock_receipt, tracked_address)
    
    assert len(swaps) == 1
    assert swaps[0].token_address == transfer_log["address"].lower()
    assert swaps[0].token_amount == 1000 * 10**18
    assert swaps[0].eth_spent_wei == 5 * 10**17
    assert swaps[0].dex == "Uniswap V2"
    assert swaps[0].log_index == 2
