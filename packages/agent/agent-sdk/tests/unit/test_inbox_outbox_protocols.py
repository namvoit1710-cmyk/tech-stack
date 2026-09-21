from agent_sdk.layer1_domain.entities.inbox_record import InboxRecord
from agent_sdk.layer1_domain.entities.outbox_record import OutboxRecord
from agent_sdk.layer2_application.interfaces.inbox_repository import IInboxRepository
from agent_sdk.layer2_application.interfaces.outbox_repository import IOutboxRepository


class _InMemoryInboxRepository:
    def __init__(self):
        self._store: dict[str, InboxRecord] = {}

    def find_by_message_id(self, message_id: str) -> InboxRecord | None:
        return self._store.get(message_id)

    def save(self, record: InboxRecord) -> InboxRecord:
        self._store[record.message_id] = record
        return record

    def update_status(
        self,
        message_id: str,
        status: str,
        *,
        error: str = "",
        processed_at: str = "",
    ) -> bool:
        existing = self._store.get(message_id)
        if existing is None:
            return False
        self._store[message_id] = InboxRecord(
            message_id=existing.message_id,
            idempotency_key=existing.idempotency_key,
            payload=existing.payload,
            status=status,
            received_at=existing.received_at,
            processed_at=processed_at or existing.processed_at,
            error=error or existing.error,
        )
        return True


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
        return [r for r in self._store.values() if r.status in ("PENDING", "FAILED")][
            :limit
        ]


def _satisfies_inbox_protocol(repo: IInboxRepository) -> IInboxRepository:
    return repo


def _satisfies_outbox_protocol(repo: IOutboxRepository) -> IOutboxRepository:
    return repo


class TestIInboxRepositoryProtocol:
    def test_in_memory_stub_satisfies_protocol(self):
        repo = _InMemoryInboxRepository()
        assert _satisfies_inbox_protocol(repo) is repo

    def test_save_and_find(self):
        repo = _InMemoryInboxRepository()
        record = InboxRecord(
            message_id="msg-1",
            idempotency_key="idem-1",
            payload={"type": "test"},
            status="RECEIVED",
            received_at="2026-01-01T00:00:00+00:00",
        )
        saved = repo.save(record)
        assert saved == record
        found = repo.find_by_message_id("msg-1")
        assert found == record

    def test_find_missing_returns_none(self):
        repo = _InMemoryInboxRepository()
        assert repo.find_by_message_id("nonexistent") is None

    def test_update_status(self):
        repo = _InMemoryInboxRepository()
        record = InboxRecord(
            message_id="msg-1",
            idempotency_key="idem-1",
            payload={},
            status="RECEIVED",
            received_at="t",
        )
        repo.save(record)
        result = repo.update_status("msg-1", "COMPLETED", processed_at="t2")
        assert result is True
        updated = repo.find_by_message_id("msg-1")
        assert updated is not None
        assert updated.status == "COMPLETED"
        assert updated.processed_at == "t2"

    def test_update_status_missing_returns_false(self):
        repo = _InMemoryInboxRepository()
        assert repo.update_status("nonexistent", "COMPLETED") is False


class TestIOutboxRepositoryProtocol:
    def test_in_memory_stub_satisfies_protocol(self):
        repo = _InMemoryOutboxRepository()
        assert _satisfies_outbox_protocol(repo) is repo

    def test_save_and_find_pending(self):
        repo = _InMemoryOutboxRepository()
        record = OutboxRecord(
            outbox_id="out-1",
            topic="agent.responses",
            message={"status": "success"},
            status="PENDING",
            created_at="t",
        )
        saved = repo.save(record)
        assert saved == record
        pending = repo.find_pending()
        assert len(pending) == 1
        assert pending[0].outbox_id == "out-1"

    def test_mark_published(self):
        repo = _InMemoryOutboxRepository()
        record = OutboxRecord(
            outbox_id="out-1",
            topic="t",
            message={},
            status="PENDING",
        )
        repo.save(record)
        result = repo.mark_published("out-1", "2026-01-01T00:00:01+00:00")
        assert result is True
        pending = repo.find_pending()
        assert len(pending) == 0

    def test_mark_failed(self):
        repo = _InMemoryOutboxRepository()
        record = OutboxRecord(
            outbox_id="out-1",
            topic="t",
            message={},
            status="PENDING",
        )
        repo.save(record)
        result = repo.mark_failed("out-1")
        assert result is True

    def test_mark_published_missing_returns_false(self):
        repo = _InMemoryOutboxRepository()
        assert repo.mark_published("nonexistent", "t") is False

    def test_find_pending_limit(self):
        repo = _InMemoryOutboxRepository()
        for i in range(5):
            repo.save(
                OutboxRecord(
                    outbox_id=f"out-{i}",
                    topic="t",
                    message={},
                    status="PENDING",
                )
            )
        assert len(repo.find_pending(limit=3)) == 3
