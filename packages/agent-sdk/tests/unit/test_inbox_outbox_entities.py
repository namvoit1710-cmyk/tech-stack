from agent_sdk.layer1_domain.entities.inbox_record import InboxRecord
from agent_sdk.layer1_domain.entities.outbox_record import OutboxRecord


class TestInboxRecord:
    def test_create_with_required_fields(self):
        record = InboxRecord(
            message_id="msg-1",
            idempotency_key="idem-1",
            payload={"type": "test"},
            status="RECEIVED",
            received_at="2026-05-29T00:00:00+00:00",
        )
        assert record.message_id == "msg-1"
        assert record.idempotency_key == "idem-1"
        assert record.payload == {"type": "test"}
        assert record.status == "RECEIVED"
        assert record.received_at == "2026-05-29T00:00:00+00:00"

    def test_defaults(self):
        record = InboxRecord(
            message_id="msg-1",
            idempotency_key="idem-1",
            payload={},
            status="RECEIVED",
            received_at="2026-05-29T00:00:00+00:00",
        )
        assert record.processed_at == ""
        assert record.error == ""

    def test_frozen(self):
        record = InboxRecord(
            message_id="msg-1",
            idempotency_key="idem-1",
            payload={},
            status="RECEIVED",
            received_at="2026-05-29T00:00:00+00:00",
        )
        try:
            record.status = "COMPLETED"  # type: ignore[misc]
            assert False, "Should have raised"
        except AttributeError:
            pass

    def test_equality(self):
        a = InboxRecord(
            message_id="msg-1",
            idempotency_key="idem-1",
            payload={"k": "v"},
            status="RECEIVED",
            received_at="t",
        )
        b = InboxRecord(
            message_id="msg-1",
            idempotency_key="idem-1",
            payload={"k": "v"},
            status="RECEIVED",
            received_at="t",
        )
        assert a == b


class TestOutboxRecord:
    def test_create_with_required_fields(self):
        record = OutboxRecord(
            outbox_id="out-1",
            topic="agent.responses",
            message={"status": "success"},
        )
        assert record.outbox_id == "out-1"
        assert record.topic == "agent.responses"
        assert record.message == {"status": "success"}

    def test_defaults(self):
        record = OutboxRecord(
            outbox_id="out-1",
            topic="t",
            message={},
        )
        assert record.key == ""
        assert record.status == "PENDING"
        assert record.created_at == ""
        assert record.published_at == ""
        assert record.attempts == 0

    def test_frozen(self):
        record = OutboxRecord(
            outbox_id="out-1",
            topic="t",
            message={},
        )
        try:
            record.status = "PUBLISHED"  # type: ignore[misc]
            assert False, "Should have raised"
        except AttributeError:
            pass

    def test_equality(self):
        a = OutboxRecord(outbox_id="out-1", topic="t", message={"k": "v"})
        b = OutboxRecord(outbox_id="out-1", topic="t", message={"k": "v"})
        assert a == b

    def test_with_all_fields(self):
        record = OutboxRecord(
            outbox_id="out-1",
            topic="agent.responses",
            message={"status": "ok"},
            key="corr-1",
            status="PUBLISHED",
            created_at="2026-05-29T00:00:00+00:00",
            published_at="2026-05-29T00:00:01+00:00",
            attempts=1,
        )
        assert record.key == "corr-1"
        assert record.status == "PUBLISHED"
        assert record.attempts == 1
