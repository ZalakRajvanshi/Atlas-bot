"""Async engine + session management."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.db.models import Base

log = logging.getLogger(__name__)

_url = settings.sqlalchemy_url
_is_sqlite = _url.startswith("sqlite")

engine = create_async_engine(
    _url,
    echo=False,
    pool_pre_ping=not _is_sqlite,
    # Render's free Postgres caps connections; stay well under it.
    **({} if _is_sqlite else {"pool_size": 5, "max_overflow": 5}),
)

SessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope. Commits on success, rolls back on error."""
    session = SessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_db() -> None:
    """Create tables if absent.

    Deliberately using create_all rather than migrations: this is a prototype
    with a single deployment target, and Alembic would be ceremony without
    benefit here. The models are additive-only so far.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("Database ready (%s)", "sqlite" if _is_sqlite else "postgres")


async def dispose_db() -> None:
    await engine.dispose()
