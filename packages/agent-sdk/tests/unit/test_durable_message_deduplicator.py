from agent_sdk.layer1_domain.entities.inbox_record import InboxRecord
from agent_sdk.layer2_application.services.durable_message_deduplicator import (
    DurableMessageDeduplicator,
)


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


class TestDurableMessageDeduplicator:
    def test_unseen_message_is_not_duplicate(self):
        repo = _InMemoryInboxRepository()
        dedup = DurableMessageDeduplicator(repo)
        assert dedup.is_duplicate("msg-1") is False

    def test_received_message_is_not_duplicate(self):
        repo = _InMemoryInboxRepository()
        dedup = DurableMessageDeduplicator(repo)
        dedup.mark_received("msg-1", "msg-1", {"type": "test"})
        assert dedup.is_duplicate("msg-1") is False

    def test_completed_message_is_duplicate(self):
        repo = _InMemoryInboxRepository()
        dedup = DurableMessageDeduplicator(repo)
        dedup.mark_received("msg-1", "msg-1", {"type": "test"})
        dedup.mark_completed("msg-1")
        assert dedup.is_duplicate("msg-1") is True

    def test_failed_message_is_not_duplicate(self):
        repo = _InMemoryInboxRepository()
        dedup = DurableMessageDeduplicator(repo)
        dedup.mark_received("msg-1", "msg-1", {"type": "test"})
        dedup.mark_failed("msg-1", "error")
        assert dedup.is_duplicate("msg-1") is False

    def test_mark_received_creates_inbox_record(self):
        repo = _InMemoryInboxRepository()
        dedup = DurableMessageDeduplicator(repo)
        record = dedup.mark_received("msg-1", "idem-1", {"type": "test"})
        assert record.message_id == "msg-1"
        assert record.idempotency_key == "idem-1"
        assert record.status == "RECEIVED"
        assert record.received_at != ""
        assert record.payload == {"type": "test"}

    def test_mark_completed_updates_status(self):
        repo = _InMemoryInboxRepository()
        dedup = DurableMessageDeduplicator(repo)
        dedup.mark_received("msg-1", "msg-1", {})
        result = dedup.mark_completed("msg-1")
        assert result is True
        record = repo.find_by_message_id("msg-1")
        assert record is not None
        assert record.status == "COMPLETED"
        assert record.processed_at != ""

    def test_mark_failed_updates_status_with_error(self):
        repo = _InMemoryInboxRepository()
        dedup = DurableMessageDeduplicator(repo)
        dedup.mark_received("msg-1", "msg-1", {})
        result = dedup.mark_failed("msg-1", "Something went wrong")
        assert result is True
        record = repo.find_by_message_id("msg-1")
        assert record is not None
        assert record.status == "FAILED"
        assert record.error == "Something went wrong"
        assert record.processed_at != ""

    def test_mark_completed_missing_returns_false(self):
        repo = _InMemoryInboxRepository()
        dedup = DurableMessageDeduplicator(repo)
        assert dedup.mark_completed("nonexistent") is False

    def test_mark_failed_missing_returns_false(self):
        repo = _InMemoryInboxRepository()
        dedup = DurableMessageDeduplicator(repo)
        assert dedup.mark_failed("nonexistent", "err") is False
