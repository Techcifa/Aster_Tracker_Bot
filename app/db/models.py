"""
app/db/models.py — SQLAlchemy ORM models matching the schema from the spec.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class TrackedWallet(Base):
    __tablename__ = "tracked_wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(42), unique=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="wallet", cascade="all, delete-orphan"
    )


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "wallet_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    wallet_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tracked_wallets.id", ondelete="CASCADE"), nullable=False
    )

    # Notification toggles
    notify_mint: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_buy_nft: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_sell_nft: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_list_nft: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_token_buy: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Value filter: mute events below this threshold (in ETH)
    min_value_eth: Mapped[float] = mapped_column(Numeric(precision=18, scale=8), default=0)

    user: Mapped["User"] = relationship(back_populates="subscriptions")
    wallet: Mapped["TrackedWallet"] = relationship(back_populates="subscriptions")


class SeenEvent(Base):
    """
    Idempotency guard.
    event_key = "tx_hash:log_index" for on-chain events
              = "list:<order_hash>"  for OpenSea listings
    """
    __tablename__ = "seen_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
