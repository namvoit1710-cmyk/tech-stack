"""SQLAlchemy workflow repository."""
import asyncio
from typing import Optional
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.layer4_frameworks.repositories.base_repository import BaseRepository
from app.layer4_frameworks.orm.entities.workflow_entity import WorkflowORMEntity
from app.layer1_domain.entities.workflow import Workflow


class WorkflowRepository(BaseRepository[Workflow, WorkflowORMEntity]):
    """SQLAlchemy implementation of IWorkflowRepository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(session_factory, WorkflowORMEntity)

    def save(self, workflow: Workflow) -> None:
        orm = WorkflowORMEntity.from_domain(workflow)
        asyncio.get_event_loop().run_until_complete(self._save(orm))

    def get_by_id(self, id: str) -> Optional[Workflow]:
        orm = asyncio.get_event_loop().run_until_complete(self._get_by_id(id))
        return orm.to_domain() if orm else None

    def list(self) -> list[Workflow]:
        orms = asyncio.get_event_loop().run_until_complete(self._list_all())
        return [o.to_domain() for o in orms]

    def delete(self, id: str) -> None:
        asyncio.get_event_loop().run_until_complete(self._delete_by_id(id))
