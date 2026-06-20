"""
app/db/crud.py — All database helper functions.

Every function accepts an AsyncSession and performs a single logical operation.
Callers are responsible for acquiring a session via get_session() and committing.
"""
from __future__ import annotations

from typing import Sequence

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SeenEvent, Subscription, TrackedWallet, User


# ── Users ─────────────────────────────────────────────────────────────────────

async def upsert_user(session: AsyncSession, chat_id: int) -> User:
    """Insert user if not present; return the User row."""
    stmt = (
        pg_insert(User)
        .values(telegram_chat_id=chat_id)
        .on_conflict_do_nothing(index_elements=["telegram_chat_id"])
        .returning(User)
    )
    result = await session.execute(stmt)
    await session.flush()
    row = result.scalar_one_or_none()
    if row is None:
        row = await session.scalar(
            select(User).where(User.telegram_chat_id == chat_id)
        )
    return row  # type: ignore[return-value]


async def get_user_by_chat_id(session: AsyncSession, chat_id: int) -> User | None:
    return await session.scalar(
        select(User).where(User.telegram_chat_id == chat_id)
    )


# ── Tracked Wallets ───────────────────────────────────────────────────────────

async def upsert_wallet(
    session: AsyncSession, address: str, label: str | None = None
) -> TrackedWallet:
    """Insert wallet if not present (address is the unique key)."""
    stmt = (
        pg_insert(TrackedWallet)
        .values(address=address, label=label)
        .on_conflict_do_update(
            index_elements=["address"],
            set_={"label": label} if label else {"address": address},
        )
        .returning(TrackedWallet)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalar_one()


async def get_wallet_by_address(
    session: AsyncSession, address: str
) -> TrackedWallet | None:
    return await session.scalar(
        select(TrackedWallet).where(TrackedWallet.address == address)
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
    stmt = (
        pg_insert(Subscription)
        .values(user_id=user_id, wallet_id=wallet_id)
        .on_conflict_do_nothing(index_elements=["user_id", "wallet_id"])
        .returning(Subscription)
    )
    result = await session.execute(stmt)
    await session.flush()
    row = result.scalar_one_or_none()
    if row is None:
        row = await session.scalar(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.wallet_id == wallet_id,
            )
        )
    return row  # type: ignore[return-value]


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
    """Return all (User, Subscription) pairs for a given wallet address."""
    result = await session.execute(
        select(User, Subscription)
        .join(Subscription, Subscription.user_id == User.id)
        .join(TrackedWallet, TrackedWallet.id == Subscription.wallet_id)
        .where(TrackedWallet.address == wallet_address)
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
    stmt = (
        pg_insert(SeenEvent)
        .values(event_key=event_key, event_type=event_type)
        .on_conflict_do_nothing(index_elements=["event_key"])
        .returning(SeenEvent.id)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalar_one_or_none() is not None
