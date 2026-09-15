"""SA-2051 (R4) — the liveness sweeper's store half: sweep_stale.

Two-phase sweep: MARK a healthy row silent longer than stale_after as unhealthy;
REMOVE any row silent longer than remove_after. `emptied` names the worker_types
this sweep drove from >0 healthy to 0 (the catalog.changed trigger). Age is via
_parse_ts, NEVER a raw-string compare (the SA-2058 cap-eviction defect).
"""

import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer4_frameworks.repositories.memory_worker_registry_store import (
    MemoryWorkerRegistryStore,
)


def _reg(instance_id, worker_type="http-request-worker", health="healthy",
         hb_ago_seconds=0, spec="1.0.0"):
    """One replica whose last_heartbeat is `hb_ago_seconds` in the past."""
    hb = (datetime.now(timezone.utc) - timedelta(seconds=hb_ago_seconds)).isoformat()
    return WorkerRegistration(
        instance_id=instance_id,
        worker_type=worker_type,
        health_status=health,
        last_heartbeat=hb,
        spec_version=spec,
    )


class TestSweepStale:
    def test_stale_is_marked_unhealthy_not_removed(self):
        """Silent > STALE but < REMOVE: marked, kept (dispatch stops immediately)."""
        store = MemoryWorkerRegistryStore()
        store.register(_reg("p0", worker_type="t", hb_ago_seconds=200))

        result = store.sweep_stale(120, 600)

        assert result.marked_count == 1
        assert result.removed_count == 0
        assert len(store.list_all()) == 1
        assert store.get("p0:t").health_status == "unhealthy"

    def test_fresh_is_untouched(self):
        """Silent < STALE: not marked, not removed, still healthy."""
        store = MemoryWorkerRegistryStore()
        store.register(_reg("p0", worker_type="t", hb_ago_seconds=50))

        result = store.sweep_stale(120, 600)

        assert result.marked_count == 0
        assert result.removed_count == 0
        assert store.get("p0:t").health_status == "healthy"

    def test_one_dead_among_live_does_not_empty_the_type(self):
        """One dead replica among live ones must NOT emit / empty the type."""
        store = MemoryWorkerRegistryStore()
        store.register(_reg("dead", worker_type="t", hb_ago_seconds=200))
        store.register(_reg("live", worker_type="t", hb_ago_seconds=10))

        result = store.sweep_stale(120, 600)

        assert result.emptied == []
        assert store.count_healthy("t") == 1

    def test_all_dead_empties_the_type(self):
        """Last healthy replica marked -> 1..N->0 transition -> emptied names the type."""
        store = MemoryWorkerRegistryStore()
        store.register(_reg("d0", worker_type="t", hb_ago_seconds=200, spec="2.0.0"))
        store.register(_reg("d1", worker_type="t", hb_ago_seconds=200, spec="2.0.0"))

        result = store.sweep_stale(120, 600)

        assert ("t", "2.0.0") in result.emptied
        assert store.count_healthy("t") == 0

    def test_type_already_empty_does_not_re_emit(self):
        """A type already at 0 healthy on the next sweep produces no new mark, so
        emptied stays empty -- this is what makes 'emitted exactly once' true."""
        store = MemoryWorkerRegistryStore()
        store.register(_reg("d0", worker_type="t", hb_ago_seconds=200))
        store.register(_reg("d1", worker_type="t", hb_ago_seconds=200))

        first = store.sweep_stale(120, 600)
        assert ("t", "1.0.0") in first.emptied

        second = store.sweep_stale(120, 600)
        assert second.emptied == []
        assert second.marked_count == 0

    def test_removal_of_already_unhealthy_row_emits_nothing(self):
        """A row marked unhealthy in a prior sweep is already excluded from
        count_healthy, so its later REMOVE changes no count -> emits nothing."""
        store = MemoryWorkerRegistryStore()
        store.register(_reg("p0", worker_type="t", health="unhealthy", hb_ago_seconds=700))

        result = store.sweep_stale(120, 600)

        assert result.removed_count == 1
        assert result.emptied == []
        assert len(store.list_all()) == 0

    def test_removal_of_still_healthy_ancient_row_empties_the_type(self):
        """The edge: a never-heartbeat replica crosses REMOVE before any sweep marked
        it. Its REMOVAL is the 1->0 transition, so emptied must still fire."""
        store = MemoryWorkerRegistryStore()
        store.register(_reg("p0", worker_type="t", health="healthy", hb_ago_seconds=700))

        result = store.sweep_stale(120, 600)

        assert result.removed_count == 1
        assert "t" in [wt for wt, _ in result.emptied]
        assert store.count_healthy("t") == 0

    def test_hana_format_heartbeat_is_not_misjudged_stale(self):
        """SA-2058 regression teeth: a HANA-format last_heartbeat
        ("2026-07-20 12:00:00", space separator, tz-naive) is FRESH here. A raw-string
        compare would sort the space-format below the isoformat cutoff (space 0x20 <
        'T' 0x54 at index 10) and wrongly mark it -- sweep_stale uses _parse_ts."""
        store = MemoryWorkerRegistryStore()
        hb = (datetime.now(timezone.utc) - timedelta(seconds=50)).strftime("%Y-%m-%d %H:%M:%S")
        store.register(WorkerRegistration(instance_id="p0", worker_type="t", last_heartbeat=hb))

        result = store.sweep_stale(120, 600)

        assert result.marked_count == 0
        assert store.get("p0:t").health_status == "healthy"

    def test_blank_heartbeat_is_maximally_stale(self):
        """Blank last_heartbeat parses to datetime.min -> a never-heartbeat worker is swept."""
        store = MemoryWorkerRegistryStore()
        store.register(WorkerRegistration(instance_id="p0", worker_type="t", last_heartbeat=""))

        result = store.sweep_stale(120, 600)

        assert result.removed_count == 1
        assert len(store.list_all()) == 0

    def test_stale_then_heartbeat_self_heals(self):
        """Marked stale, then a heartbeat resets ts + status -> next sweep no-ops."""
        store = MemoryWorkerRegistryStore()
        store.register(_reg("p0", worker_type="t", hb_ago_seconds=200))

        first = store.sweep_stale(120, 600)
        assert first.marked_count == 1
        assert store.get("p0:t").health_status == "unhealthy"

        store.heartbeat("p0:t")
        assert store.get("p0:t").health_status == "healthy"

        second = store.sweep_stale(120, 600)
        assert second.marked_count == 0
