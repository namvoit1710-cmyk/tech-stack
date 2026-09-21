from __future__ import annotations

from datetime import datetime, timezone

from agent_sdk.layer1_domain.entities.inbox_record import InboxRecord
from agent_sdk.layer2_application.interfaces.inbox_repository import (
    IInboxRepository,
)


class DurableMessageDeduplicator:
    def __init__(self, inbox_repository: IInboxRepository) -> None:
        self._inbox_repository = inbox_repository

    def is_duplicate(self, message_id: str) -> bool:
        record = self._inbox_repository.find_by_message_id(message_id)
        return record is not None and record.status == "COMPLETED"

    def mark_received(
        self, message_id: str, idempotency_key: str, payload: dict
    ) -> InboxRecord:
        now = datetime.now(timezone.utc).isoformat()
        record = InboxRecord(
            message_id=message_id,
            idempotency_key=idempotency_key,
            payload=payload,
            status="RECEIVED",
            received_at=now,
        )
        return self._inbox_repository.save(record)

    def mark_completed(self, message_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        return self._inbox_repository.update_status(
            message_id, "COMPLETED", processed_at=now
        )

    def mark_failed(self, message_id: str, error: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        return self._inbox_repository.update_status(
            message_id, "FAILED", error=error, processed_at=now
        )
