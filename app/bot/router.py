"""
app/bot/router.py — Core message and callback query handlers for the Telegram Bot.
"""
from __future__ import annotations

import logging
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from web3 import Web3

from app.config import get_settings
from app.db import crud, get_session
from app.bot.keyboards import get_filters_keyboard, get_min_value_keyboard
from app.services import alchemy_api

logger = logging.getLogger(__name__)
router = Router(name="bot_router")


@router.message(Command("start"))
async def handle_start(message: Message):
    """
    Handle /start command. Register the user and display the welcome message.
    """
    async with get_session() as session:
        await crud.upsert_user(session, message.chat.id)
        await session.commit()
    
    await message.answer(
        "👋 *Welcome to Aster Tracker!*\n\n"
        "I can monitor Ethereum wallets in real-time and notify you about:\n"
        "• NFT Mints\n"
        "• NFT Buys/Sells (secondary market)\n"
        "• DEX Token Buys (swaps)\n"
        "• NFT Listings\n\n"
        "Commands:\n"
        "• `/track <address> [label]` — Start monitoring a wallet\n"
        "• `/untrack <address>` — Stop monitoring a wallet\n"
        "• `/list` — List all tracked wallets\n"
        "• `/filters <address>` — Configure notifications for a wallet\n"
        "• `/help` — Show this message again\n\n"
        "Use /track to get started!",
        parse_mode="Markdown"
    )


@router.message(Command("help"))
async def handle_help(message: Message):
    """
    Handle /help command. Show detailed commands usage.
    """
    await message.answer(
        "📋 *Aster Tracker Command Reference:*\n\n"
        "• `/track <address> [label]` — Start tracking an Ethereum wallet address with an optional description.\n"
        "• `/untrack <address>` — Stop tracking an address and remove notifications.\n"
        "• `/list` — View all wallets you are currently tracking.\n"
        "• `/filters <address>` — Toggle notification categories (Mints, Buys, Sells, Listings, Token Swaps) or configure a minimum value threshold.\n"
        "• `/help` — Display this command reference.\n\n"
        "All on-chain actions are tracked as close to instantly as possible.",
        parse_mode="Markdown"
    )


@router.message(Command("track"))
async def handle_track(message: Message, command: CommandObject):
    """
    Handle /track <address> [label] command.
    Checksum-validate the address, subscribe the user, and update the Alchemy webhook.
    """
    args = command.args
    if not args:
        await message.answer("Usage: `/track <ethereum_address> [label]`", parse_mode="Markdown")
        return
        
    parts = args.split(maxsplit=1)
    address_str = parts[0].strip()
    label = parts[1].strip() if len(parts) > 1 else None
    
    try:
        address = Web3.to_checksum_address(address_str)
    except ValueError:
        await message.answer("❌ Invalid Ethereum address format. Please provide a valid hex address.")
        return
        
    async with get_session() as session:
        user = await crud.upsert_user(session, message.chat.id)
        wallet = await crud.upsert_wallet(session, address, label)
        await crud.create_subscription(session, user.id, wallet.id)
        await session.commit()
        
    # Dynamically update the Alchemy address webhook
    settings = get_settings()
    success = await alchemy_api.add_address(
        auth_token=settings.alchemy_auth_token,
        webhook_id=settings.alchemy_webhook_id,
        address=address,
    )
    
    if success:
        label_info = f"\nLabel: {label}" if label else ""
        await message.answer(f"✅ Successfully tracking wallet: `{address}`{label_info}", parse_mode="Markdown")
    else:
        label_info = f"\nLabel: {label}" if label else ""
        await message.answer(
            f"⚠️ Tracked wallet locally: `{address}`{label_info}\n"
            "Failed to register with the on-chain webhook tracker. Our admin team will look into it.",
            parse_mode="Markdown"
        )


@router.message(Command("untrack"))
async def handle_untrack(message: Message, command: CommandObject):
    """
    Handle /untrack <address> command.
    Stop tracking the address, delete subscription, and clean up webhook if no one else tracks it.
    """
    args = command.args
    if not args:
        await message.answer("Usage: `/untrack <ethereum_address>`", parse_mode="Markdown")
        return
        
    address_str = args.strip()
    try:
        address = Web3.to_checksum_address(address_str)
    except ValueError:
        await message.answer("❌ Invalid Ethereum address format.")
        return
        
    async with get_session() as session:
        user = await crud.get_user_by_chat_id(session, message.chat.id)
        if not user:
            await message.answer("You are not tracking any wallets yet.")
            return
            
        wallet = await crud.get_wallet_by_address(session, address)
        if not wallet:
            await message.answer("This wallet is not tracked in our system.")
            return
            
        sub = await crud.get_subscription(session, user.id, wallet.id)
        if not sub:
            await message.answer("You are not tracking this wallet.")
            return
            
        await crud.delete_subscription(session, user.id, wallet.id)
        sub_count = await crud.count_subscribers(session, wallet.id)
        
        # Clean up tracked wallet if no subscribers remain
        if sub_count == 0:
            await session.delete(wallet)
        await session.commit()
        
    # Remove from Alchemy webhook if no active subscribers remain
    if sub_count == 0:
        settings = get_settings()
        await alchemy_api.remove_address(
            auth_token=settings.alchemy_auth_token,
            webhook_id=settings.alchemy_webhook_id,
            address=address,
        )
        
    await message.answer(f"❌ Stopped tracking wallet: `{address}`", parse_mode="Markdown")


@router.message(Command("list"))
async def handle_list(message: Message):
    """
    Handle /list command. List all tracked wallets with labels.
    """
    async with get_session() as session:
        subs = await crud.get_user_subscriptions(session, message.chat.id)
        
    if not subs:
        await message.answer("You are not tracking any wallets yet. Use /track to add one!")
        return
        
    text = "Tracked Wallets:\n"
    for i, (_, wallet) in enumerate(subs, 1):
        trunc_addr = f"{wallet.address[:6]}...{wallet.address[-4:]}"
        label_str = f"[{wallet.label}] " if wallet.label else ""
        text += f"{i}. {label_str}(`{trunc_addr}`)\n"
        
    await message.answer(text, parse_mode="Markdown")


@router.message(Command("filters"))
async def handle_filters(message: Message, command: CommandObject):
    """
    Handle /filters <address> command. Display inline settings panel.
    """
    args = command.args
    if not args:
        await message.answer("Usage: `/filters <ethereum_address>`", parse_mode="Markdown")
        return
        
    address_str = args.strip()
    try:
        address = Web3.to_checksum_address(address_str)
    except ValueError:
        await message.answer("❌ Invalid Ethereum address format.")
        return
        
    async with get_session() as session:
        user = await crud.get_user_by_chat_id(session, message.chat.id)
        if not user:
            await message.answer("You are not tracking any wallets yet.")
            return
            
        wallet = await crud.get_wallet_by_address(session, address)
        if not wallet:
            await message.answer("This wallet is not tracked in our system.")
            return
            
        sub = await crud.get_subscription(session, user.id, wallet.id)
        if not sub:
            await message.answer("You are not tracking this wallet.")
            return
            
        kb = get_filters_keyboard(sub, address)
        await message.answer(
            f"⚙️ *Filters for {address[:8]}...{address[-6:]}:*\n"
            "Use the buttons below to toggle what notifications you receive for this wallet:",
            reply_markup=kb,
            parse_mode="Markdown"
        )


@router.callback_query(F.data.startswith("toggle:"))
async def handle_toggle_filter(callback: CallbackQuery):
    """
    Handle button clicks to toggle specific event types.
    """
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer()
        return
        
    field = parts[1]
    address = parts[2]
    
    async with get_session() as session:
        user = await crud.get_user_by_chat_id(session, callback.message.chat.id)
        if not user:
            await callback.answer("User not found.")
            return
        wallet = await crud.get_wallet_by_address(session, address)
        if not wallet:
            await callback.answer("Wallet not found.")
            return
        sub = await crud.get_subscription(session, user.id, wallet.id)
        if not sub:
            await callback.answer("Subscription not found.")
            return
            
        # Toggle boolean filters
        if field == "mint":
            sub.notify_mint = not sub.notify_mint
        elif field == "buy_nft":
            sub.notify_buy_nft = not sub.notify_buy_nft
        elif field == "sell_nft":
            sub.notify_sell_nft = not sub.notify_sell_nft
        elif field == "list_nft":
            sub.notify_list_nft = not sub.notify_list_nft
        elif field == "token_buy":
            sub.notify_token_buy = not sub.notify_token_buy
            
        await session.commit()
        kb = get_filters_keyboard(sub, address)
        
    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer("Filter updated!")


@router.callback_query(F.data.startswith("min_val_menu:"))
async def handle_min_val_menu(callback: CallbackQuery):
    """
    Display preset selection panel for the minimum value filter.
    """
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer()
        return
        
    address = parts[1]
    kb = get_min_value_keyboard(address)
    await callback.message.edit_text(
        f"Select a minimum transaction value threshold (in ETH) for `{address}`.\n"
        "Transactions below this value will be muted.",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_min:"))
async def handle_set_min(callback: CallbackQuery):
    """
    Set minimum value filter based on button preset clicks.
    """
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer()
        return
        
    val_str = parts[1]
    address = parts[2]
    val = float(val_str)
    
    async with get_session() as session:
        user = await crud.get_user_by_chat_id(session, callback.message.chat.id)
        if not user:
            await callback.answer("User not found.")
            return
        wallet = await crud.get_wallet_by_address(session, address)
        if not wallet:
            await callback.answer("Wallet not found.")
            return
        sub = await crud.get_subscription(session, user.id, wallet.id)
        if not sub:
            await callback.answer("Subscription not found.")
            return
            
        sub.min_value_eth = val
        await session.commit()
        kb = get_filters_keyboard(sub, address)
        
    await callback.message.edit_text(
        f"⚙️ *Filters for {address[:8]}...{address[-6:]}:*\n"
        "Use the buttons below to toggle what notifications you receive for this wallet:",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await callback.answer(f"Min value set to {val} ETH")


@router.callback_query(F.data.startswith("back_to_filters:"))
async def handle_back_to_filters(callback: CallbackQuery):
    """
    Navigate back to filters overview panel from preset selector.
    """
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer()
        return
        
    address = parts[1]
    
    async with get_session() as session:
        user = await crud.get_user_by_chat_id(session, callback.message.chat.id)
        if not user:
            await callback.answer("User not found.")
            return
        wallet = await crud.get_wallet_by_address(session, address)
        if not wallet:
            await callback.answer("Wallet not found.")
            return
        sub = await crud.get_subscription(session, user.id, wallet.id)
        if not sub:
            await callback.answer("Subscription not found.")
            return
            
        kb = get_filters_keyboard(sub, address)
        
    await callback.message.edit_text(
        f"⚙️ *Filters for {address[:8]}...{address[-6:]}:*\n"
        "Use the buttons below to toggle what notifications you receive for this wallet:",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "close_menu")
async def handle_close_menu(callback: CallbackQuery):
    """
    Delete filter panel completely.
    """
    await callback.message.delete()
    await callback.answer()
