"""ORM entity for StageRecord."""
import json
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text
from typing import Optional

from app.layer4_frameworks.orm.entities.base_entity import BaseORMEntity
from app.layer1_domain.entities.stage_record import StageRecord


class StageRecordORMEntity(BaseORMEntity):
    __tablename__ = "aiw_stage_history"

    run_id: Mapped[str] = mapped_column(String(36), default="")
    node_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    from_status: Mapped[str] = mapped_column(String(50), default="")
    to_status: Mapped[str] = mapped_column(String(50), default="")
    event_type: Mapped[str] = mapped_column(String(100), default="")
    timestamp: Mapped[str] = mapped_column(String(50), default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    def to_domain(self) -> StageRecord:
        return StageRecord(
            id=self.id,
            run_id=self.run_id,
            node_id=self.node_id,
            task_id=self.task_id,
            from_status=self.from_status,
            to_status=self.to_status,
            event_type=self.event_type,
            timestamp=self.timestamp,
            metadata=json.loads(self.metadata_json) if self.metadata_json else {},
        )

    @classmethod
    def from_domain(cls, entity: StageRecord) -> "StageRecordORMEntity":
        return cls(
            id=entity.id,
            run_id=entity.run_id,
            node_id=entity.node_id,
            task_id=entity.task_id,
            from_status=entity.from_status,
            to_status=entity.to_status,
            event_type=entity.event_type,
            timestamp=entity.timestamp,
            metadata_json=json.dumps(entity.metadata),
        )
