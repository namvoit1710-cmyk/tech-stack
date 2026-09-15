"""SQLAlchemy async engine and session configuration."""
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.layer4_frameworks.config.app_config import settings
from typing import Any


class DatabaseSessionManager:
    """Manages the async SQLAlchemy engine and session factory."""

    def __init__(self, database_url: str | None = None) -> None:
        url = database_url or settings.DATABASE_URL
        self.engine = create_async_engine(url, echo=False)
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    def get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self.session_factory

    async def create_tables(self, base: Any) -> None:
        """Create all tables from the ORM base metadata."""
        async with self.engine.begin() as conn:
            await conn.run_sync(base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()
