"""SQLAlchemy stage history repository."""
import asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from sqlalchemy import select

from app.layer4_frameworks.repositories.base_repository import BaseRepository
from app.layer4_frameworks.orm.entities.stage_record_entity import StageRecordORMEntity
from app.layer1_domain.entities.stage_record import StageRecord
from typing import Any


class StageHistoryRepository(BaseRepository[StageRecord, StageRecordORMEntity]):
    """SQLAlchemy implementation of IStageHistoryRepository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(session_factory, StageRecordORMEntity)

    def save(self, record: StageRecord) -> None:
        orm = StageRecordORMEntity.from_domain(record)
        asyncio.get_event_loop().run_until_complete(self._save(orm))

    def list_by_run(self, run_id: str) -> list[StageRecord]:
        async def _list() -> Any:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(StageRecordORMEntity).where(
                        StageRecordORMEntity.run_id == run_id
                    )
                )
                return [o.to_domain() for o in result.scalars().all()]
        return asyncio.get_event_loop().run_until_complete(_list())

    def list_by_task(self, task_id: str) -> list[StageRecord]:
        async def _list() -> Any:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(StageRecordORMEntity).where(
                        StageRecordORMEntity.task_id == task_id
                    )
                )
                return [o.to_domain() for o in result.scalars().all()]
        return asyncio.get_event_loop().run_until_complete(_list())
