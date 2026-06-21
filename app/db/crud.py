"""
app/db/crud.py — All database helper functions.

Every function accepts an AsyncSession and performs a single logical operation.
Callers are responsible for acquiring a session via get_session() and committing.
"""
from __future__ import annotations

from typing import Sequence

from sqlalchemy import delete, func, insert as sa_insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SeenEvent, Subscription, TrackedWallet, User


# ── Users ─────────────────────────────────────────────────────────────────────

async def upsert_user(session: AsyncSession, chat_id: int) -> User:
    """Insert user if not present; return the User row."""
    # Use dialect-agnostic upsert: try to select first, then insert if missing.
    row = await session.scalar(
        select(User).where(User.telegram_chat_id == chat_id)
    )
    if row is None:
        row = User(telegram_chat_id=chat_id)
        session.add(row)
        await session.flush()
    return row


async def get_user_by_chat_id(session: AsyncSession, chat_id: int) -> User | None:
    return await session.scalar(
        select(User).where(User.telegram_chat_id == chat_id)
    )


# ── Tracked Wallets ───────────────────────────────────────────────────────────

async def upsert_wallet(
    session: AsyncSession, address: str, label: str | None = None
) -> TrackedWallet:
    """Insert wallet if not present (address is the unique key)."""
    row = await session.scalar(
        select(TrackedWallet).where(TrackedWallet.address == address)
    )
    if row is None:
        row = TrackedWallet(address=address, label=label)
        session.add(row)
        await session.flush()
    elif label:
        row.label = label
        await session.flush()
    return row


async def get_wallet_by_address(
    session: AsyncSession, address: str
) -> TrackedWallet | None:
    # Case-insensitive: OpenSea sends lowercase, DB stores checksummed.
    return await session.scalar(
        select(TrackedWallet).where(
            func.lower(TrackedWallet.address) == address.lower()
        )
    )


async def get_all_tracked_addresses(session: AsyncSession) -> list[str]:
    """Return all unique addresses currently being tracked by at least one user."""
    result = await session.execute(select(TrackedWallet.address))
    return list(result.scalars().all())


async def get_wallets_with_listing_subscribers(
    session: AsyncSession,
) -> Sequence[TrackedWallet]:
    """Return wallets where at least one subscriber has notify_list_nft=True."""
    result = await session.execute(
        select(TrackedWallet)
        .join(Subscription, Subscription.wallet_id == TrackedWallet.id)
        .where(Subscription.notify_list_nft.is_(True))
        .distinct()
    )
    return result.scalars().all()


# ── Subscriptions ─────────────────────────────────────────────────────────────

async def create_subscription(
    session: AsyncSession, user_id: int, wallet_id: int
) -> Subscription:
    row = await session.scalar(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.wallet_id == wallet_id,
        )
    )
    if row is None:
        row = Subscription(user_id=user_id, wallet_id=wallet_id)
        session.add(row)
        await session.flush()
    return row


async def delete_subscription(
    session: AsyncSession, user_id: int, wallet_id: int
) -> None:
    await session.execute(
        delete(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.wallet_id == wallet_id,
        )
    )
    await session.flush()


async def count_subscribers(session: AsyncSession, wallet_id: int) -> int:
    """Count active subscribers for a wallet (used to decide Alchemy cleanup)."""
    result = await session.execute(
        select(Subscription).where(Subscription.wallet_id == wallet_id)
    )
    return len(result.scalars().all())


async def get_user_subscriptions(
    session: AsyncSession, chat_id: int
) -> list[tuple[Subscription, TrackedWallet]]:
    """Return all (Subscription, TrackedWallet) pairs for a given Telegram user."""
    result = await session.execute(
        select(Subscription, TrackedWallet)
        .join(User, User.id == Subscription.user_id)
        .join(TrackedWallet, TrackedWallet.id == Subscription.wallet_id)
        .where(User.telegram_chat_id == chat_id)
    )
    return list(result.tuples().all())


async def get_subscription(
    session: AsyncSession, user_id: int, wallet_id: int
) -> Subscription | None:
    return await session.scalar(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.wallet_id == wallet_id,
        )
    )


async def get_subscribers_for_wallet(
    session: AsyncSession, wallet_address: str
) -> list[tuple[User, Subscription]]:
    """Return all (User, Subscription) pairs for a given wallet address.
    Case-insensitive: OpenSea sends lowercase, DB stores checksummed.
    """
    result = await session.execute(
        select(User, Subscription)
        .join(Subscription, Subscription.user_id == User.id)
        .join(TrackedWallet, TrackedWallet.id == Subscription.wallet_id)
        .where(func.lower(TrackedWallet.address) == wallet_address.lower())
    )
    return list(result.tuples().all())


# ── Seen Events (Idempotency) ─────────────────────────────────────────────────

async def try_insert_seen_event(
    session: AsyncSession, event_key: str, event_type: str
) -> bool:
    """
    Attempt to insert an event_key. Returns True if newly inserted (first time
    seeing this event), False if already exists (duplicate — skip notification).
    """
    existing = await session.scalar(
        select(SeenEvent.id).where(SeenEvent.event_key == event_key)
    )
    if existing is not None:
        return False
    session.add(SeenEvent(event_key=event_key, event_type=event_type))
    await session.flush()
    return True
