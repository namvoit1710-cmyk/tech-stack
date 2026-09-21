import asyncio
from unittest.mock import AsyncMock, MagicMock

from agent_sdk.layer1_domain.entities.outbox_record import OutboxRecord
from agent_sdk.layer2_application.services.outbox_publisher import OutboxPublisher


class _InMemoryOutboxRepository:
    def __init__(self):
        self._store: dict[str, OutboxRecord] = {}

    def save(self, record: OutboxRecord) -> OutboxRecord:
        self._store[record.outbox_id] = record
        return record

    def mark_published(self, outbox_id: str, published_at: str) -> bool:
        existing = self._store.get(outbox_id)
        if existing is None:
            return False
        self._store[outbox_id] = OutboxRecord(
            outbox_id=existing.outbox_id,
            topic=existing.topic,
            message=existing.message,
            key=existing.key,
            status="PUBLISHED",
            created_at=existing.created_at,
            published_at=published_at,
            attempts=existing.attempts,
        )
        return True

    def mark_failed(self, outbox_id: str) -> bool:
        existing = self._store.get(outbox_id)
        if existing is None:
            return False
        self._store[outbox_id] = OutboxRecord(
            outbox_id=existing.outbox_id,
            topic=existing.topic,
            message=existing.message,
            key=existing.key,
            status="FAILED",
            created_at=existing.created_at,
            published_at=existing.published_at,
            attempts=existing.attempts + 1,
        )
        return True

    def find_pending(self, *, limit: int = 50) -> list[OutboxRecord]:
        return [r for r in self._store.values() if r.status == "PENDING"][:limit]


class TestOutboxPublisherPublish:
    def test_publish_writes_outbox_and_relays(self):
        repo = _InMemoryOutboxRepository()
        inner = AsyncMock()
        publisher = OutboxPublisher(outbox_repository=repo, inner_publisher=inner)
        asyncio.run(publisher.publish("t", {"k": "v"}, "key-1"))
        inner.publish.assert_called_once_with("t", {"k": "v"}, "key-1")
        assert len(repo._store) == 1
        record = next(iter(repo._store.values()))
        assert record.topic == "t"
        assert record.message == {"k": "v"}
        assert record.key == "key-1"

    def test_publish_marks_published_on_success(self):
        repo = _InMemoryOutboxRepository()
        inner = AsyncMock()
        publisher = OutboxPublisher(outbox_repository=repo, inner_publisher=inner)
        asyncio.run(publisher.publish("t", {"k": "v"}))
        record = next(iter(repo._store.values()))
        assert record.status == "PUBLISHED"
        assert record.published_at != ""

    def test_publish_marks_failed_on_relay_error(self):
        repo = _InMemoryOutboxRepository()
        inner = AsyncMock()
        inner.publish.side_effect = RuntimeError("broker down")
        logger = MagicMock()
        publisher = OutboxPublisher(
            outbox_repository=repo, inner_publisher=inner, logger=logger
        )
        # Should NOT raise — message is safe in outbox
        asyncio.run(publisher.publish("t", {"k": "v"}))
        record = next(iter(repo._store.values()))
        assert record.status == "FAILED"
        assert record.attempts == 1
        logger.warning.assert_called_once()


class TestOutboxPublisherFlush:
    def test_flush_pending_retries_pending_records(self):
        repo = _InMemoryOutboxRepository()
        inner = AsyncMock()
        publisher = OutboxPublisher(outbox_repository=repo, inner_publisher=inner)
        # Create a PENDING record directly
        pending = OutboxRecord(
            outbox_id="out-1",
            topic="t",
            message={"k": "v"},
            key="key-1",
            status="PENDING",
            created_at="t",
        )
        repo.save(pending)
        flushed = asyncio.run(publisher.flush_pending())
        assert flushed == 1
        inner.publish.assert_called_once_with("t", {"k": "v"}, "key-1")
        record = repo._store["out-1"]
        assert record.status == "PUBLISHED"

    def test_flush_pending_marks_failed_on_error(self):
        repo = _InMemoryOutboxRepository()
        inner = AsyncMock()
        inner.publish.side_effect = RuntimeError("broker down")
        publisher = OutboxPublisher(outbox_repository=repo, inner_publisher=inner)
        pending = OutboxRecord(
            outbox_id="out-1",
            topic="t",
            message={},
            status="PENDING",
        )
        repo.save(pending)
        flushed = asyncio.run(publisher.flush_pending())
        assert flushed == 0
        assert repo._store["out-1"].status == "FAILED"


class TestOutboxPublisherClose:
    def test_close_delegates_to_inner(self):
        inner = AsyncMock()
        publisher = OutboxPublisher(
            outbox_repository=MagicMock(), inner_publisher=inner
        )
        asyncio.run(publisher.close())
        inner.close.assert_called_once()


class TestOutboxPublisherProtocol:
    def test_satisfies_imessage_publisher_protocol(self):
        from agent_sdk.layer2_application.interfaces.message_publisher import (
            IMessagePublisher,
        )

        repo = _InMemoryOutboxRepository()
        inner = AsyncMock()
        publisher = OutboxPublisher(outbox_repository=repo, inner_publisher=inner)

        def _check(p: IMessagePublisher) -> IMessagePublisher:
            return p

        assert _check(publisher) is publisher
