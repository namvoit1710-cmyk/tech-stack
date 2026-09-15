"""Phase 3 Sub 3.2 — outbox event shapes + deterministic id helper."""

from __future__ import annotations

from app.layer1_domain.events import (
    EventTopic,
    OutboxRequested,
    OutboxResolved,
)
from app.layer1_domain.value_objects.event_id import EventId


class TestOutboxEvents:
    def test_outbox_requested_default_construction(self):
        evt = OutboxRequested()
        assert evt.outbox_id == ""
        assert evt.kind == ""
        assert evt.payload == {}
        assert evt.idempotency_key == ""
        assert evt.event_id  # default factory

    def test_outbox_requested_round_trip_payload(self):
        evt = OutboxRequested(
            outbox_id="ob_1",
            run_id="r_1",
            workflow_id="wf_1",
            kind="file_upload",
            payload={"filename": "a.csv", "content_hash": "abc"},
            idempotency_key="abc",
        )
        assert evt.kind == "file_upload"
        assert evt.payload["content_hash"] == "abc"

    def test_outbox_resolved_ok_outcome(self):
        evt = OutboxResolved(
            outbox_id="ob_1",
            outcome="ok",
            result={"file_id": "f_xyz"},
        )
        assert evt.outcome == "ok"
        assert evt.error is None
        assert evt.result["file_id"] == "f_xyz"

    def test_outbox_resolved_failed_carries_error(self):
        evt = OutboxResolved(
            outbox_id="ob_1",
            outcome="failed",
            error="permanent: bad payload",
        )
        assert evt.outcome == "failed"
        assert evt.error == "permanent: bad payload"

    def test_topic_constants(self):
        assert str(EventTopic.OUTBOX_REQUESTED) == "outbox.requested"
        assert str(EventTopic.OUTBOX_RESOLVED) == "outbox.resolved"


class TestDeterministicOutboxId:
    def test_same_inputs_produce_same_id(self):
        a = EventId.derive_outbox("r_1", "t_1", 0, "file_upload")
        b = EventId.derive_outbox("r_1", "t_1", 0, "file_upload")
        assert a == b

    def test_different_kind_produces_different_id(self):
        a = EventId.derive_outbox("r_1", "t_1", 0, "file_upload")
        b = EventId.derive_outbox("r_1", "t_1", 0, "file_delete")
        assert a != b

    def test_id_is_short_hex(self):
        a = EventId.derive_outbox("r_1", "t_1", 0, "file_upload")
        assert len(a) == 16
        int(a, 16)  # parses as hex
