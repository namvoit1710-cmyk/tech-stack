"""Stage history recording: every state transition is logged for audit."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from app.layer1_domain.entities.stage_record import StageRecord

if TYPE_CHECKING:
    from app.layer2_application.repositories.stage_history_repository_interface import IStageHistoryRepository


class StageRecorder:

    def __init__(self, repository: IStageHistoryRepository) -> None:
        self._repo = repository

    def record(
        self,
        *,
        run_id: str,
        event_type: str,
        from_status: str,
        to_status: str,
        node_id: str | None = None,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StageRecord:
        record = StageRecord(
            run_id=run_id,
            node_id=node_id,
            task_id=task_id,
            from_status=from_status,
            to_status=to_status,
            event_type=event_type,
            metadata=metadata or {},
        )
        self._repo.save(record)
        return record
