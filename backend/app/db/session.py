"""Async SQLAlchemy engine/session factory.

Uses a single engine per process; in tests ``DATABASE_URL`` points at SQLite
(aiosqlite) so the suite runs without Docker, while dev/staging use Postgres.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.db.models import Base

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _to_async_url(url: str) -> str:
    """Normalise sync driver URLs to their async equivalents."""
    if url.startswith("postgresql+psycopg://") or url.startswith("postgresql://"):
        return url.replace("postgresql+psycopg://", "postgresql+asyncpg://").replace(
            "postgresql://", "postgresql+asyncpg://"
        )
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///")
    return url


def get_engine():
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        url = _to_async_url(settings.database_url)
        kwargs = {"echo": False}
        if url.startswith("postgresql"):
            kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True)
        _engine = create_async_engine(url, **kwargs)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def init_models() -> None:
    """Create tables when missing (dev convenience; Alembic owns prod schema)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
