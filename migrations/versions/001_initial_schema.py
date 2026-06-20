"""initial schema

Revision ID: 001_initial_schema
Revises: None
Create Date: 2026-06-20 12:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. users table
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_chat_id"),
    )

    # 2. tracked_wallets table
    op.create_table(
        "tracked_wallets",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("address", sa.String(length=42), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("address"),
    )

    # 3. subscriptions table
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("wallet_id", sa.Integer(), nullable=False),
        sa.Column("notify_mint", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("notify_buy_nft", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("notify_sell_nft", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("notify_list_nft", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("notify_token_buy", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("min_value_eth", sa.Numeric(precision=18, scale=8), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wallet_id"], ["tracked_wallets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "wallet_id"),
    )

    # 4. seen_events table
    op.create_table(
        "seen_events",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key"),
    )


def downgrade() -> None:
    op.drop_table("seen_events")
    op.drop_table("subscriptions")
    op.drop_table("tracked_wallets")
    op.drop_table("users")
