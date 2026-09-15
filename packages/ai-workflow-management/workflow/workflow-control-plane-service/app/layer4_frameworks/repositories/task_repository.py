"""SQLAlchemy task repository."""
import asyncio
from typing import Any, Optional
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from sqlalchemy import select

from app.layer4_frameworks.repositories.base_repository import BaseRepository
from app.layer4_frameworks.orm.entities.task_entity import TaskORMEntity
from app.layer1_domain.entities.task import Task


class TaskRepository(BaseRepository[Task, TaskORMEntity]):
    """SQLAlchemy implementation of ITaskRepository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(session_factory, TaskORMEntity)

    def save(self, task: Task) -> None:
        orm = TaskORMEntity.from_domain(task)
        asyncio.get_event_loop().run_until_complete(self._save(orm))

    def get_by_id(self, id: str) -> Optional[Task]:
        orm = asyncio.get_event_loop().run_until_complete(self._get_by_id(id))
        return orm.to_domain() if orm else None

    def list_by_run(self, run_id: str) -> list[Task]:
        async def _list() -> Any:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(TaskORMEntity).where(TaskORMEntity.run_id == run_id)
                )
                return [o.to_domain() for o in result.scalars().all()]
        return asyncio.get_event_loop().run_until_complete(_list())

    def latest_by_run_node(self, run_id: str, node_id: str) -> Optional[Task]:
        """Newest task for one node of one run — see ``ITaskRepository``.

        Present so this backend still satisfies the protocol. Note it cannot
        currently serve the orchestration engine either way: that caller awaits
        ``alatest_by_run_node`` / ``alist_by_run``, and this class carries no
        AsyncRepoMixin, so the sqlite backend has never had the async variants
        at all. That gap predates SA-2318 and is not addressed here.
        """
        async def _latest() -> Any:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(TaskORMEntity)
                    .where(TaskORMEntity.run_id == run_id)
                    .where(TaskORMEntity.node_id == node_id)
                    .order_by(TaskORMEntity.created_at.desc())
                    .limit(1)
                )
                orm = result.scalars().first()
                return orm.to_domain() if orm else None
        return asyncio.get_event_loop().run_until_complete(_latest())

    def update_status(self, id: str, status: str) -> None:
        async def _update() -> None:
            async with self._session_factory() as session:
                orm = await session.get(TaskORMEntity, id)
                if orm:
                    orm.status = status
                    await session.commit()
        asyncio.get_event_loop().run_until_complete(_update())
