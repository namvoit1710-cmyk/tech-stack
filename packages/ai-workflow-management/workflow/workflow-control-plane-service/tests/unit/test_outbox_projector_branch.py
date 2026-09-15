"""Phase 3 Sub 3.2 — projector reducer branches for outbox events.

Verifies ``OutboxRequested`` / ``OutboxResolved`` fold into
``ProjectedRunState.pending_outbox`` / ``failed_outbox`` correctly,
and that the snapshot codec round-trips both fields.
"""

from __future__ import annotations

from app.layer2_application.projection import (
    ProjectedRunState,
)
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)


_seq = 0


def _env(event_type: str, **payload) -> EventLogEnvelope:
    global _seq
    _seq += 1
    return EventLogEnvelope(
        event_id=f"{event_type}-{_seq}",
        run_id="r_1",
        topic=event_type.lower(),
        event_type=event_type,
        timestamp="2026-04-25T00:00:00Z",
        payload={"run_id": "r_1", **payload},
    )


class TestOutboxRequested:
    def test_adds_pending_entry(self):
        state = ProjectedRunState().apply(
            _env(
                "OutboxRequested",
                outbox_id="ob_1",
                kind="file_upload",
                idempotency_key="k1",
            ),
        )
        assert "ob_1" in state.pending_outbox
        entry = state.pending_outbox["ob_1"]
        assert entry["kind"] == "file_upload"
        assert entry["idempotency_key"] == "k1"

    def test_redelivery_is_idempotent(self):
        state = ProjectedRunState()
        for _ in range(3):
            state = state.apply(
                _env("OutboxRequested", outbox_id="ob_1", kind="file_upload"),
            )
        assert len(state.pending_outbox) == 1

    def test_missing_outbox_id_is_noop(self):
        state = ProjectedRunState().apply(
            _env("OutboxRequested", kind="file_upload"),
        )
        assert state.pending_outbox == {}


class TestOutboxResolved:
    def _with_pending(self) -> ProjectedRunState:
        return ProjectedRunState().apply(
            _env("OutboxRequested", outbox_id="ob_1", kind="file_upload"),
        )

    def test_ok_clears_pending(self):
        state = self._with_pending().apply(
            _env("OutboxResolved", outbox_id="ob_1", outcome="ok"),
        )
        assert state.pending_outbox == {}
        assert state.failed_outbox == {}

    def test_failed_moves_to_failed_outbox(self):
        state = self._with_pending().apply(
            _env(
                "OutboxResolved",
                outbox_id="ob_1",
                outcome="failed",
                error="bad payload",
            ),
        )
        assert state.pending_outbox == {}
        assert "ob_1" in state.failed_outbox
        assert state.failed_outbox["ob_1"]["error"] == "bad payload"

    def test_resolved_for_unknown_outbox_id_is_safe(self):
        state = ProjectedRunState().apply(
            _env("OutboxResolved", outbox_id="ob_unknown", outcome="ok"),
        )
        assert state.pending_outbox == {}
        assert state.failed_outbox == {}


class TestSnapshotCodec:
    def test_schema_version_bumped(self):
        # 1→2 added outbox fields; 2→3 (SA-1371) added pending_timers.
        assert ProjectedRunState.SCHEMA_VERSION == 3

    def test_round_trip_pending_and_failed(self):
        state = ProjectedRunState(
            run_id="r_1",
            pending_outbox={"ob_1": {"kind": "file_upload", "requested_at": "t"}},
            failed_outbox={"ob_2": {"kind": "stage_record", "error": "x"}},
        )
        decoded = ProjectedRunState.from_json(state.to_json())
        assert decoded.pending_outbox == state.pending_outbox
        assert decoded.failed_outbox == state.failed_outbox

    def test_decode_missing_fields_defaults_empty(self):
        # Older v1 snapshot lacking the outbox fields.
        legacy = (
            '{"run_id":"r_1","workflow_id":"wf","status":"RUNNING",'
            '"started_at":"","completed_at":null,"error":null,'
            '"output_data":null,"completed_nodes":[],"skipped_nodes":[],'
            '"child_runs":{},"variables":{},"input_data":{}}'
        )
        decoded = ProjectedRunState.from_json(legacy)
        assert decoded.pending_outbox == {}
        assert decoded.failed_outbox == {}
