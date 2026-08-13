from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.exceptions import ConfigurationError


def normalize_database_url(value: str) -> str:
    """Return a SQLAlchemy async URL for Neon/PostgreSQL or local SQLite."""
    url = value.strip()
    if not url:
        raise ConfigurationError("DATABASE_URL is not configured")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("sqlite://") and not url.startswith("sqlite+aiosqlite://"):
        return "sqlite+aiosqlite://" + url.removeprefix("sqlite://")
    return url


class Database:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None

    @property
    def configured(self) -> bool:
        return bool(self.database_url.strip())

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            url = normalize_database_url(self.database_url)
            backend = make_url(url).get_backend_name()
            options: dict = {"pool_pre_ping": True}
            if backend == "postgresql":
                # Vercel instances are short lived, so keep each local pool deliberately small.
                options.update(pool_size=2, max_overflow=3, pool_recycle=300)
            self._engine = create_async_engine(url, **options)
            self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if self._sessions is None:
            _ = self.engine
        assert self._sessions is not None
        async with self._sessions() as session:
            yield session

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
