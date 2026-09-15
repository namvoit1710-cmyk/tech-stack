"""Phase 3 Sub 3.2 — MemoryOutboxRepository contract tests.

Covers ``list_unresolved`` filtering, ``record_attempt`` /
``get_attempts`` / ``reset_attempts`` lifecycle, and behaviour when
``OutboxResolved`` arrives after an ``OutboxRequested``.
"""

from __future__ import annotations

from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer4_frameworks.persistence.memory.memory_outbox_repository import (
    MemoryOutboxRepository,
)


def _envelope(
    event_type: str,
    run_id: str,
    *,
    event_id: str,
    payload: dict,
    timestamp: str = "2026-04-25T00:00:00Z",
    topic: str = "outbox.requested",
) -> EventLogEnvelope:
    return EventLogEnvelope(
        event_id=event_id,
        run_id=run_id,
        topic=topic,
        event_type=event_type,
        timestamp=timestamp,
        payload=payload,
    )


def _build_repo() -> tuple[MemoryEventLogRepository, MemoryOutboxRepository]:
    log = MemoryEventLogRepository()
    repo = MemoryOutboxRepository(log)
    return log, repo


class TestListUnresolved:
    def test_empty_when_no_runs_marked(self):
        _log, repo = _build_repo()
        assert repo.list_unresolved() == []

    def test_returns_outbox_requested_for_marked_run(self):
        log, repo = _build_repo()
        repo.mark_run("r_1")
        log.append(_envelope(
            "OutboxRequested", "r_1",
            event_id="e_1",
            payload={
                "outbox_id": "ob_1",
                "kind": "file_upload",
                "workflow_id": "wf_1",
                "payload": {"filename": "a.csv"},
                "idempotency_key": "abc",
            },
        ))
        entries = repo.list_unresolved()
        assert len(entries) == 1
        e = entries[0]
        assert e.outbox_id == "ob_1"
        assert e.kind == "file_upload"
        assert e.workflow_id == "wf_1"
        assert e.idempotency_key == "abc"
        assert e.payload == {"filename": "a.csv"}
        assert e.run_id == "r_1"
        assert e.sequence > 0

    def test_excludes_resolved_entries(self):
        log, repo = _build_repo()
        repo.mark_run("r_1")
        log.append(_envelope(
            "OutboxRequested", "r_1", event_id="e_1",
            payload={"outbox_id": "ob_1", "kind": "file_delete"},
        ))
        log.append(_envelope(
            "OutboxResolved", "r_1", event_id="e_2",
            payload={"outbox_id": "ob_1", "outcome": "ok"},
            topic="outbox.resolved",
        ))
        assert repo.list_unresolved() == []

    def test_returns_unresolved_when_one_of_two_resolved(self):
        log, repo = _build_repo()
        repo.mark_run("r_1")
        log.append(_envelope(
            "OutboxRequested", "r_1", event_id="e_1",
            payload={"outbox_id": "ob_1", "kind": "file_upload"},
        ))
        log.append(_envelope(
            "OutboxRequested", "r_1", event_id="e_2",
            payload={"outbox_id": "ob_2", "kind": "file_delete"},
        ))
        log.append(_envelope(
            "OutboxResolved", "r_1", event_id="e_3",
            payload={"outbox_id": "ob_1", "outcome": "ok"},
            topic="outbox.resolved",
        ))
        entries = repo.list_unresolved()
        assert [e.outbox_id for e in entries] == ["ob_2"]

    def test_since_sequence_filters_older_entries(self):
        log, repo = _build_repo()
        repo.mark_run("r_1")
        log.append(_envelope(
            "OutboxRequested", "r_1", event_id="e_1",
            payload={"outbox_id": "ob_1", "kind": "file_upload"},
        ))
        first_seq = log.list_by_run("r_1")[0].sequence
        log.append(_envelope(
            "OutboxRequested", "r_1", event_id="e_2",
            payload={"outbox_id": "ob_2", "kind": "file_upload"},
        ))
        entries = repo.list_unresolved(since_sequence=first_seq)
        assert [e.outbox_id for e in entries] == ["ob_2"]

    def test_limit_caps_returned_entries(self):
        log, repo = _build_repo()
        repo.mark_run("r_1")
        for i in range(5):
            log.append(_envelope(
                "OutboxRequested", "r_1", event_id=f"e_{i}",
                payload={"outbox_id": f"ob_{i}", "kind": "file_upload"},
            ))
        entries = repo.list_unresolved(limit=2)
        assert len(entries) == 2

    def test_marks_idempotent(self):
        _log, repo = _build_repo()
        repo.mark_run("r_1")
        repo.mark_run("r_1")
        repo.mark_run("")  # no-op for empty id
        # No exception → success.

    def test_skips_run_not_marked(self):
        log, repo = _build_repo()
        log.append(_envelope(
            "OutboxRequested", "r_other", event_id="e_x",
            payload={"outbox_id": "ob_x", "kind": "file_upload"},
        ))
        # No mark_run("r_other") — so dispatcher does not see it.
        assert repo.list_unresolved() == []


class TestAttemptCounter:
    def test_record_attempt_increments(self):
        _log, repo = _build_repo()
        assert repo.record_attempt("ob_1") == 1
        assert repo.record_attempt("ob_1") == 2
        assert repo.record_attempt("ob_2") == 1

    def test_get_attempts_default_zero(self):
        _log, repo = _build_repo()
        assert repo.get_attempts("ob_unknown") == 0

    def test_reset_clears_counter(self):
        _log, repo = _build_repo()
        repo.record_attempt("ob_1")
        repo.record_attempt("ob_1")
        assert repo.get_attempts("ob_1") == 2
        repo.reset_attempts("ob_1")
        assert repo.get_attempts("ob_1") == 0

    def test_reset_unknown_is_noop(self):
        _log, repo = _build_repo()
        repo.reset_attempts("ob_unknown")  # no exception
        assert repo.get_attempts("ob_unknown") == 0
