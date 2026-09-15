"""SQLAlchemy run repository."""
import asyncio
from typing import Any, Optional
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from sqlalchemy import select

from app.layer4_frameworks.repositories.base_repository import BaseRepository
from app.layer4_frameworks.orm.entities.workflow_run_entity import WorkflowRunORMEntity
from app.layer1_domain.entities.workflow_run import WorkflowRun


class RunRepository(BaseRepository[WorkflowRun, WorkflowRunORMEntity]):
    """SQLAlchemy implementation of IRunRepository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(session_factory, WorkflowRunORMEntity)

    def save(self, run: WorkflowRun) -> None:
        orm = WorkflowRunORMEntity.from_domain(run)
        asyncio.get_event_loop().run_until_complete(self._save(orm))

    def get_by_id(self, id: str) -> Optional[WorkflowRun]:
        orm = asyncio.get_event_loop().run_until_complete(self._get_by_id(id))
        return orm.to_domain() if orm else None

    def update_status(self, id: str, status: str) -> None:
        async def _update() -> None:
            async with self._session_factory() as session:
                orm = await session.get(WorkflowRunORMEntity, id)
                if orm:
                    orm.status = status
                    await session.commit()
        asyncio.get_event_loop().run_until_complete(_update())

    def list_by_workflow(self, workflow_id: str) -> list[WorkflowRun]:
        async def _list() -> Any:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(WorkflowRunORMEntity).where(
                        WorkflowRunORMEntity.workflow_id == workflow_id
                    )
                )
                return [o.to_domain() for o in result.scalars().all()]
        return asyncio.get_event_loop().run_until_complete(_list())
