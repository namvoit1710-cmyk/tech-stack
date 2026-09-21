from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agent_sdk.layer1_domain.entities.outbox_record import OutboxRecord
from agent_sdk.layer2_application.interfaces.message_publisher import (
    IMessagePublisher,
)
from agent_sdk.layer2_application.interfaces.outbox_repository import (
    IOutboxRepository,
)


class OutboxPublisher:
    def __init__(
        self,
        outbox_repository: IOutboxRepository,
        inner_publisher: IMessagePublisher,
        logger: Any = None,
    ) -> None:
        self._outbox_repository = outbox_repository
        self._inner_publisher = inner_publisher
        self._logger = logger

    async def publish(self, topic: str, message: dict, key: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        record = OutboxRecord(
            outbox_id=str(uuid.uuid4()),
            topic=topic,
            message=message,
            key=key or "",
            status="PENDING",
            created_at=now,
        )
        self._outbox_repository.save(record)
        try:
            await self._inner_publisher.publish(topic, message, key)
            self._outbox_repository.mark_published(
                record.outbox_id, datetime.now(timezone.utc).isoformat()
            )
        except Exception:
            self._outbox_repository.mark_failed(record.outbox_id)
            if self._logger:
                self._logger.warning(
                    "Outbox inline relay failed, will retry on flush",
                    outbox_id=record.outbox_id,
                )

    async def flush_pending(self) -> int:
        pending = self._outbox_repository.find_pending()
        flushed = 0
        for record in pending:
            try:
                await self._inner_publisher.publish(
                    record.topic, record.message, record.key or None
                )
                self._outbox_repository.mark_published(
                    record.outbox_id, datetime.now(timezone.utc).isoformat()
                )
                flushed += 1
            except Exception:
                self._outbox_repository.mark_failed(record.outbox_id)
        return flushed

    async def close(self) -> None:
        await self._inner_publisher.close()
