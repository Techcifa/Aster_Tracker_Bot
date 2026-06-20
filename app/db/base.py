"""
app/db/base.py — SQLAlchemy async engine and session factory.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


# Engine is created lazily on first access via init_db().
_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db(database_url: str) -> None:
    """Initialise the async engine and session factory. Call once at startup."""
    global _engine, _session_factory
    is_sqlite = database_url.startswith("sqlite")
    engine_kwargs: dict = dict(echo=False, pool_pre_ping=True)
    if not is_sqlite:
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 20
    _engine = create_async_engine(database_url, **engine_kwargs)
    _session_factory = async_sessionmaker(
        _engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )


async def close_db() -> None:
    """Dispose the engine connection pool. Call on shutdown."""
    if _engine is not None:
        await _engine.dispose()


def get_session() -> AsyncSession:
    """Return a new AsyncSession from the factory."""
    if _session_factory is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _session_factory()
