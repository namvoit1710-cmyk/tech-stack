"""Base SQLAlchemy repository with common CRUD operations."""
from typing import TypeVar, Generic, Optional
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from sqlalchemy import select

D = TypeVar("D")  # Domain entity type
O = TypeVar("O")  # ORM entity type


class BaseRepository(Generic[D, O]):
    """Generic async repository with basic CRUD operations."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], orm_class: type[O]) -> None:
        self._session_factory = session_factory
        self._orm_class = orm_class

    async def _save(self, orm_entity: O) -> None:
        async with self._session_factory() as session:
            await session.merge(orm_entity)
            await session.commit()

    async def _get_by_id(self, entity_id: str) -> Optional[O]:
        async with self._session_factory() as session:
            return await session.get(self._orm_class, entity_id)

    async def _list_all(self) -> list[O]:
        async with self._session_factory() as session:
            result = await session.execute(select(self._orm_class))
            return list(result.scalars().all())

    async def _delete_by_id(self, entity_id: str) -> None:
        async with self._session_factory() as session:
            entity = await session.get(self._orm_class, entity_id)
            if entity:
                await session.delete(entity)
                await session.commit()
