"""ORM entity for Task."""
import json
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, Integer
from typing import Optional

from app.layer4_frameworks.orm.entities.base_entity import BaseORMEntity
from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects.task_status import TaskStatus


class TaskORMEntity(BaseORMEntity):
    __tablename__ = "aiw_tasks"

    run_id: Mapped[str] = mapped_column(String(36))
    node_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(50), default="READY")
    worker_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    resolved_input: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_metadata: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    started_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    completed_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    def to_domain(self) -> Task:
        return Task(
            id=self.id,
            run_id=self.run_id,
            node_id=self.node_id,
            status=TaskStatus(self.status),
            worker_type=self.worker_type,
            resolved_input=json.loads(self.resolved_input) if self.resolved_input else None,
            output_ref=self.output_ref,
            output_metadata=json.loads(self.output_metadata) if self.output_metadata else None,
            retry_count=self.retry_count,
            max_retries=self.max_retries,
            error=self.error,
            idempotency_key=self.idempotency_key,
            created_at=self.created_at or "",
            started_at=self.started_at,
            completed_at=self.completed_at,
        )

    @classmethod
    def from_domain(cls, entity: Task) -> "TaskORMEntity":
        return cls(
            id=entity.id,
            run_id=entity.run_id,
            node_id=entity.node_id,
            status=entity.status.value if hasattr(entity.status, "value") else str(entity.status),
            worker_type=entity.worker_type,
            resolved_input=json.dumps(entity.resolved_input) if entity.resolved_input else None,
            output_ref=entity.output_ref,
            output_metadata=json.dumps(entity.output_metadata) if entity.output_metadata else None,
            retry_count=entity.retry_count,
            max_retries=entity.max_retries,
            error=entity.error,
            idempotency_key=entity.idempotency_key,
            created_at=entity.created_at,
            started_at=entity.started_at,
            completed_at=entity.completed_at,
        )
