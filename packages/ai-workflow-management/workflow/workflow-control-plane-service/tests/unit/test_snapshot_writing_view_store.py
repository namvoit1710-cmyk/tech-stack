"""Unit tests for the snapshot-write hook in
``CachedProjectorRunContextViewStore`` — Phase 4.3.

Covers:
- ``SNAPSHOT_MODE=off`` → zero repo interactions.
- N-events threshold fires.
- T-seconds threshold fires once events have been folded.
- Terminal status always snapshots.
- K-back retention deletes older snapshots.
- Write failure is logged, not raised.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.layer1_domain.run_projection import (
    ProjectedRunState,
)
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer2_application.interfaces.snapshot_repository_interface import (
    Snapshot,
)
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer4_frameworks.persistence.memory.memory_snapshot_repository import (
    MemorySnapshotRepository,
)
from app.layer4_frameworks.run_context.cached_projector_view_store import (
    CachedProjectorRunContextViewStore,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _append(
    repo: MemoryEventLogRepository,
    run_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> EventLogEnvelope:
    envelope = EventLogEnvelope(
        event_id=event_id or f"evt-{event_type}-{id(payload)}",
        run_id=run_id,
        topic="wio.runs.events",
        event_type=event_type,
        timestamp="2026-04-22T00:00:00Z",
        payload=payload or {},
    )
    repo.append(envelope)
    return envelope


def _append_run_started(repo, run_id="r1", workflow_id="wf1"):
    return _append(
        repo, run_id, "RunStatusChanged",
        payload={"run_id": run_id, "workflow_id": workflow_id, "status": "started"},
    )


def _append_task_completed(repo, run_id, node_id, *, eid: str):
    return _append(
        repo, run_id, "NodeCompleted",
        payload={"run_id": run_id, "node_id": node_id},
        event_id=eid,
    )


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSnapshotModeOff:
    def test_no_snapshot_written_by_default(self):
        log = MemoryEventLogRepository()
        snaps = MemorySnapshotRepository()
        store = CachedProjectorRunContextViewStore(
            log, snapshot_repo=snaps, snapshot_mode="off",
        )
        _append_run_started(log, "r1")
        store.get("r1")
        assert snaps.load_latest("r1", min_schema_version=1) is None

    def test_no_snapshot_when_repo_missing(self):
        log = MemoryEventLogRepository()
        store = CachedProjectorRunContextViewStore(
            log, snapshot_repo=None, snapshot_mode="shadow",
        )
        _append_run_started(log, "r1")
        store.get("r1")  # must not raise


class TestNEventsTrigger:
    def test_fires_after_n_events(self):
        log = MemoryEventLogRepository()
        snaps = MemorySnapshotRepository()
        clock = FakeClock(100.0)
        store = CachedProjectorRunContextViewStore(
            log, snapshot_repo=snaps, snapshot_mode="shadow",
            snapshot_every_n_events=5,
            snapshot_every_t_seconds=3600,  # effectively disabled
            clock=clock,
        )
        _append_run_started(log, "r1")
        for i in range(5):
            _append_task_completed(log, "r1", f"n{i}", eid=f"e{i}")
        store.get("r1")
        snap = snaps.load_latest("r1", min_schema_version=ProjectedRunState.SCHEMA_VERSION)
        assert snap is not None
        assert snap.sequence == 6  # 1 RunStarted + 5 NodeCompleted
        decoded = ProjectedRunState.from_json(snap.state_json)
        assert decoded.run_id == "r1"
        assert decoded.completed_nodes == frozenset({"n0", "n1", "n2", "n3", "n4"})

    def test_does_not_fire_below_threshold(self):
        log = MemoryEventLogRepository()
        snaps = MemorySnapshotRepository()
        clock = FakeClock(100.0)
        store = CachedProjectorRunContextViewStore(
            log, snapshot_repo=snaps, snapshot_mode="shadow",
            snapshot_every_n_events=100,
            snapshot_every_t_seconds=3600,
            clock=clock,
        )
        _append_run_started(log, "r1")
        _append_task_completed(log, "r1", "n0", eid="e0")
        store.get("r1")
        assert snaps.load_latest("r1", min_schema_version=1) is None


class TestTSecondsTrigger:
    def test_fires_after_t_seconds_with_new_events(self):
        log = MemoryEventLogRepository()
        snaps = MemorySnapshotRepository()
        clock = FakeClock(100.0)
        store = CachedProjectorRunContextViewStore(
            log, snapshot_repo=snaps, snapshot_mode="shadow",
            snapshot_every_n_events=10_000,  # effectively disabled
            snapshot_every_t_seconds=60,
            clock=clock,
        )
        _append_run_started(log, "r1")
        store.get("r1")
        # First call arms the T-timer without writing (except no
        # snapshot yet). Advance clock past threshold.
        clock.advance(61.0)
        _append_task_completed(log, "r1", "n0", eid="e0")
        store.get("r1")
        snap = snaps.load_latest("r1", min_schema_version=ProjectedRunState.SCHEMA_VERSION)
        assert snap is not None
        assert snap.sequence == 2

    def test_does_not_fire_on_t_alone_without_new_events(self):
        log = MemoryEventLogRepository()
        snaps = MemorySnapshotRepository()
        clock = FakeClock(100.0)
        store = CachedProjectorRunContextViewStore(
            log, snapshot_repo=snaps, snapshot_mode="shadow",
            snapshot_every_n_events=10_000,
            snapshot_every_t_seconds=60,
            clock=clock,
        )
        _append_run_started(log, "r1")
        store.get("r1")
        clock.advance(120.0)
        store.get("r1")  # no new events; must not snapshot
        assert snaps.load_latest("r1", min_schema_version=1) is None


class TestTerminalTrigger:
    @pytest.mark.parametrize("terminal_status,expected_status", [
        ("completed", "COMPLETED"),
        ("failed", "FAILED"),
        ("cancelled", "CANCELLED"),
    ])
    def test_snapshots_on_terminal_status(self, terminal_status, expected_status):
        log = MemoryEventLogRepository()
        snaps = MemorySnapshotRepository()
        store = CachedProjectorRunContextViewStore(
            log, snapshot_repo=snaps, snapshot_mode="shadow",
            snapshot_every_n_events=10_000,
            snapshot_every_t_seconds=10_000,
        )
        _append_run_started(log, "r1")
        _append(log, "r1", "RunStatusChanged", payload={"status": terminal_status})
        store.get("r1")
        snap = snaps.load_latest("r1", min_schema_version=ProjectedRunState.SCHEMA_VERSION)
        assert snap is not None
        assert ProjectedRunState.from_json(snap.state_json).status == expected_status


class TestRetention:
    def test_keep_k_back_drops_older(self):
        log = MemoryEventLogRepository()
        snaps = MemorySnapshotRepository()
        clock = FakeClock(100.0)
        store = CachedProjectorRunContextViewStore(
            log, snapshot_repo=snaps, snapshot_mode="shadow",
            snapshot_every_n_events=2,
            snapshot_every_t_seconds=10_000,
            snapshot_keep_k_back=2,
            clock=clock,
        )
        _append_run_started(log, "r1")
        # Produce three threshold crossings: at seq 3, 5, 7.
        for burst in range(3):
            for i in range(2):
                _append_task_completed(
                    log, "r1", f"n{burst}-{i}", eid=f"e{burst}-{i}",
                )
            store.get("r1")

        # K=2 → keep seq 5 and 7; seq 3 should be dropped.
        bucket = snaps._by_run["r1"]
        sequences = sorted(s.sequence for s in bucket)
        assert sequences == [5, 7]


class TestWriteFailureIsolation:
    def test_write_failure_logged_not_raised(self, caplog):
        class FailingRepo:
            def save(self, snapshot):  # pragma: no cover — raised
                raise RuntimeError("boom")

            def load_latest(self, run_id, *, min_schema_version):
                return None

            def delete_older_than(self, run_id, keep_sequence):
                return 0

        log = MemoryEventLogRepository()
        store = CachedProjectorRunContextViewStore(
            log, snapshot_repo=FailingRepo(), snapshot_mode="shadow",
            snapshot_every_n_events=1,
        )
        _append_run_started(log, "r1")
        with caplog.at_level(logging.WARNING):
            view = store.get("r1")
        assert view is not None
        assert any("snapshot write failed" in rec.message for rec in caplog.records)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
