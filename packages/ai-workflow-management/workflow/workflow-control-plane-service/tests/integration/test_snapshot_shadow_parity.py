"""Phase 4.6 — shadow-mode parity integration.

Under ``SNAPSHOT_MODE=shadow``:
- Full-log replay is authoritative (returned view).
- Snapshot + tail replay runs in parallel and is diffed.
- Matching states emit ``outcome=ok``; divergence emits
  ``outcome=diverged`` and logs a warning.

These tests seed a deliberately divergent snapshot and verify the
comparator catches it and does NOT corrupt the returned view.
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
from app.layer4_frameworks.monitoring.in_memory import InMemoryMonitor
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer4_frameworks.persistence.memory.memory_snapshot_repository import (
    MemorySnapshotRepository,
)
from app.layer4_frameworks.run_context.cached_projector_view_store import (
    CachedProjectorRunContextViewStore,
)


def _ev(log, run_id, event_type, payload=None, eid=""):
    log.append(EventLogEnvelope(
        event_id=eid or f"{event_type}-{log._sequence.get(run_id, 0) + 1}",
        run_id=run_id,
        topic="wio.runs.events",
        event_type=event_type,
        timestamp="2026-04-22T00:00:00Z",
        payload=payload or {},
    ))


def _seed(log, run_id, n_tasks=5):
    _ev(log, run_id, "RunStatusChanged",
        payload={"run_id": run_id, "workflow_id": f"wf-{run_id}", "status": "started"})
    for i in range(n_tasks):
        _ev(log, run_id, "NodeCompleted",
            payload={"run_id": run_id, "node_id": f"n{i}"},
            eid=f"tc-{i}")


class TestShadowMatchingSnapshot:
    def test_matching_snapshot_emits_ok_counter(self):
        log = MemoryEventLogRepository()
        snaps = MemorySnapshotRepository()
        monitor = InMemoryMonitor()
        _seed(log, "r1", n_tasks=3)

        # Seed a snapshot that matches the state at seq 2 (RunStarted
        # + 1 NodeCompleted). Replaying the tail (NodeCompleted n1, n2)
        # must reach the same state as the full replay.
        partial = ProjectedRunState(
            run_id="r1", workflow_id="wf-r1", status="RUNNING",
            started_at="2026-04-22T00:00:00Z",
            completed_nodes=frozenset({"n0"}),
        )
        snaps.save(Snapshot(
            run_id="r1", sequence=2, schema_version=ProjectedRunState.SCHEMA_VERSION,
            state_json=partial.to_json(),
            created_at="2026-04-22T00:00:00Z",
        ))

        store = CachedProjectorRunContextViewStore(
            log, snapshot_repo=snaps, snapshot_mode="shadow",
            monitor=monitor,
        )
        view = store.get("r1")
        assert view is not None
        assert view.completed_nodes == frozenset({"n0", "n1", "n2"})

        # Exactly one drift check emitted, with outcome=ok.
        drift = [c for c in monitor.counters if c.metric == "snapshot_drift"]
        assert len(drift) == 1
        assert drift[0].tags["outcome"] == "ok"


class TestShadowDivergentSnapshot:
    def test_divergent_snapshot_is_detected_and_logged(self, caplog):
        log = MemoryEventLogRepository()
        snaps = MemorySnapshotRepository()
        monitor = InMemoryMonitor()
        _seed(log, "r1", n_tasks=3)

        # Synthetic divergence: snapshot at seq 2 claims a WRONG
        # completed-node set. Tail replay (NodeCompleted n1, n2) will
        # still end up wrong relative to the full replay baseline.
        bogus = ProjectedRunState(
            run_id="r1", workflow_id="wf-r1", status="RUNNING",
            started_at="2026-04-22T00:00:00Z",
            completed_nodes=frozenset({"wrong-node"}),
        )
        snaps.save(Snapshot(
            run_id="r1", sequence=2, schema_version=ProjectedRunState.SCHEMA_VERSION,
            state_json=bogus.to_json(),
            created_at="2026-04-22T00:00:00Z",
        ))

        store = CachedProjectorRunContextViewStore(
            log, snapshot_repo=snaps, snapshot_mode="shadow",
            monitor=monitor,
        )
        with caplog.at_level(logging.WARNING):
            view = store.get("r1")

        # Full-replay result wins — divergent snapshot is not trusted.
        assert view.completed_nodes == frozenset({"n0", "n1", "n2"})

        drift = [c for c in monitor.counters if c.metric == "snapshot_drift"]
        assert len(drift) == 1
        assert drift[0].tags["outcome"] == "diverged"
        assert "completed_nodes" in drift[0].tags["fields"]
        assert any("shadow parity drift" in r.message for r in caplog.records)


class TestShadowNoSnapshot:
    def test_no_snapshot_emits_no_snapshot_counter(self):
        log = MemoryEventLogRepository()
        snaps = MemorySnapshotRepository()
        monitor = InMemoryMonitor()
        _seed(log, "r1", n_tasks=2)

        store = CachedProjectorRunContextViewStore(
            log, snapshot_repo=snaps, snapshot_mode="shadow",
            monitor=monitor,
        )
        view = store.get("r1")
        assert view is not None

        drift = [c for c in monitor.counters if c.metric == "snapshot_drift"]
        assert len(drift) == 1
        assert drift[0].tags["outcome"] == "no_snapshot"


class TestShadowDoesNotAffectReturnedView:
    def test_view_state_unchanged_by_shadow_check(self):
        """Shadow mode must never mutate the returned state — even with
        a divergent snapshot, the caller sees the full-replay view."""
        log = MemoryEventLogRepository()
        snaps = MemorySnapshotRepository()
        _seed(log, "r1", n_tasks=5)

        bogus = ProjectedRunState(
            run_id="r1", status="BOGUS",
            completed_nodes=frozenset({"ghost"}),
        )
        snaps.save(Snapshot(
            run_id="r1", sequence=3, schema_version=ProjectedRunState.SCHEMA_VERSION,
            state_json=bogus.to_json(),
            created_at="2026-04-22T00:00:00Z",
        ))

        store = CachedProjectorRunContextViewStore(
            log, snapshot_repo=snaps, snapshot_mode="shadow",
        )
        view = store.get("r1")
        assert view.status == "RUNNING"
        assert view.completed_nodes == frozenset({"n0", "n1", "n2", "n3", "n4"})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
