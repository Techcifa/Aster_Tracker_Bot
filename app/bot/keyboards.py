"""
app/bot/keyboards.py — Inline keyboards for bot settings and filters.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from app.db.models import Subscription


def get_filters_keyboard(subscription: Subscription, address: str) -> InlineKeyboardMarkup:
    """
    Generate the inline keyboard showing current toggle states for subscription filters.
    """
    def tick(val: bool) -> str:
        return "🟢 On" if val else "🔴 Off"

    buttons = [
        [
            InlineKeyboardButton(
                text=f"Mint NFT: {tick(subscription.notify_mint)}",
                callback_data=f"toggle:mint:{address}"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"Buy NFT: {tick(subscription.notify_buy_nft)}",
                callback_data=f"toggle:buy_nft:{address}"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"Sell NFT: {tick(subscription.notify_sell_nft)}",
                callback_data=f"toggle:sell_nft:{address}"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"List NFT: {tick(subscription.notify_list_nft)}",
                callback_data=f"toggle:list_nft:{address}"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"Token Buy: {tick(subscription.notify_token_buy)}",
                callback_data=f"toggle:token_buy:{address}"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"Min Value: {subscription.min_value_eth} ETH ⚙️",
                callback_data=f"min_val_menu:{address}"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Close Menu",
                callback_data="close_menu"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_min_value_keyboard(address: str) -> InlineKeyboardMarkup:
    """
    Generate preset options for setting the minimum ETH value filter.
    """
    presets = [0, 0.01, 0.1, 0.5, 1.0, 5.0]
    keyboard_rows = []
    
    # 2 buttons per row for presets
    for i in range(0, len(presets), 2):
        row = [
            InlineKeyboardButton(text=f"{presets[i]} ETH", callback_data=f"set_min:{presets[i]}:{address}"),
            InlineKeyboardButton(text=f"{presets[i+1]} ETH", callback_data=f"set_min:{presets[i+1]}:{address}")
        ]
        keyboard_rows.append(row)
        
    keyboard_rows.append([
        InlineKeyboardButton(text="⬅️ Back", callback_data=f"back_to_filters:{address}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
