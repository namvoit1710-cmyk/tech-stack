"""M3 + M4 acceptance (SA-2260, SA-2261): the two hot-path caches now reclaim.

M3 — ``MetadataStampingPublisher._cache`` sat on the publish hot path keyed by
``run_id`` with no cap, no TTL and no eviction, and it negative-cached a
not-found run permanently, so even misses accumulated.

M4 — ``CentralizedEventEnricher._workflow_cache`` was keyed by ``workflow_id``
with the same three omissions, and carried a second, worse defect: once a
workflow had been enriched, an edit to it was never seen again for the life of
the pod. That one is a correctness bug wearing a memory bug's clothes, so it
gets its own test.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.events import RunStatusChanged
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer4_frameworks.caching.bounded_ttl_cache import BoundedTTLCache
from app.layer4_frameworks.messaging.event_enricher import CentralizedEventEnricher
from app.layer4_frameworks.messaging.metadata_stamping_publisher import (
    MetadataStampingPublisher,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _CapturingInner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def publish(self, topic: str, event: object) -> None:
        self.calls.append((topic, event))


class _CountingRunRepo:
    """MemoryRunRepository that reports how often the cache actually missed."""

    def __init__(self) -> None:
        self._inner = MemoryRunRepository()
        self.get_calls = 0

    def save(self, run) -> None:
        self._inner.save(run)

    def get_by_id(self, run_id: str):
        self.get_calls += 1
        return self._inner.get_by_id(run_id)

    def __getattr__(self, item):  # pragma: no cover - passthrough
        return getattr(self._inner, item)


# ----------------------------------------------------------------------
# M3 — SA-2260
# ----------------------------------------------------------------------


class TestMetadataStampingCacheIsBounded:
    def test_ten_thousand_distinct_run_ids_stay_bounded(self):
        """The acceptance criterion, stated literally."""
        repo = _CountingRunRepo()
        cache = BoundedTTLCache(maxsize=256, ttl_seconds=900, name="m3")
        pub = MetadataStampingPublisher(
            inner=_CapturingInner(), run_repo=repo, cache=cache,
        )
        for i in range(10_000):
            run_id = f"run-{i}"
            repo.save(WorkflowRun(id=run_id, workflow_id="wf", metadata={"i": i}))
            pub.publish("run.status", RunStatusChanged(
                run_id=run_id, root_run_id=run_id, status="started"))

        assert cache.size() <= 256, (
            f"{cache.size()} entries after 10k distinct run_ids — unbounded"
        )
        assert cache.stats().evictions > 0, "nothing was ever reclaimed"

    def test_a_run_with_no_metadata_is_a_cache_HIT_not_a_repeated_lookup(self):
        """``{}`` is a real cached value; treating it as a miss re-queries forever."""
        repo = _CountingRunRepo()
        repo.save(WorkflowRun(id="r-1", workflow_id="wf", metadata={}))
        pub = MetadataStampingPublisher(
            inner=_CapturingInner(), run_repo=repo,
            cache=BoundedTTLCache(maxsize=64, ttl_seconds=900),
        )
        for _ in range(50):
            pub.publish("run.status", RunStatusChanged(
                run_id="r-1", root_run_id="r-1", status="started"))
        assert repo.get_calls == 1, (
            f"{repo.get_calls} repo lookups for one run — an empty metadata "
            "dict is being read as a cache miss"
        )

    def test_a_missing_run_is_not_negative_cached_forever(self):
        """The old code stored {} permanently for a run it could not find."""
        clock = _FakeClock()
        repo = _CountingRunRepo()
        pub = MetadataStampingPublisher(
            inner=_CapturingInner(), run_repo=repo,
            cache=BoundedTTLCache(maxsize=64, ttl_seconds=900, clock=clock),
        )
        event = RunStatusChanged(run_id="late-1", root_run_id="late-1", status="started")

        pub.publish("run.status", event)                     # miss -> negative cached
        assert repo.get_calls == 1

        pub.publish("run.status", RunStatusChanged(
            run_id="late-1", root_run_id="late-1", status="started"))
        assert repo.get_calls == 1, "the short negative TTL should still be live"

        # The run row shows up (replica lag), and the negative expires.
        repo.save(WorkflowRun(id="late-1", workflow_id="wf", metadata={"src": "crm"}))
        clock.advance(11)
        stamped = RunStatusChanged(run_id="late-1", root_run_id="late-1", status="done")
        pub.publish("run.status", stamped)
        assert repo.get_calls == 2, "the negative was cached permanently"
        assert stamped.metadata == {"src": "crm"}

    def test_cache_stats_are_exposed_for_the_budget_export(self):
        pub = MetadataStampingPublisher(
            inner=_CapturingInner(), run_repo=_CountingRunRepo(),
            cache=BoundedTTLCache(maxsize=8, ttl_seconds=60, name="metadata_stamping"),
        )
        stats = pub.cache_stats()
        assert stats.name == "metadata_stamping"
        assert stats.maxsize == 8

    def test_stamping_still_works(self):
        """Never-worse: the behaviour the cache exists to support is unchanged."""
        repo = _CountingRunRepo()
        repo.save(WorkflowRun(id="r-9", workflow_id="wf", metadata={"trace": "t-1"}))
        inner = _CapturingInner()
        pub = MetadataStampingPublisher(inner=inner, run_repo=repo)
        event = RunStatusChanged(run_id="r-9", root_run_id="r-9", status="started")
        pub.publish("run.status", event)
        assert inner.calls[0][1].metadata == {"trace": "t-1"}


# ----------------------------------------------------------------------
# M4 — SA-2261
# ----------------------------------------------------------------------


def _workflow(name: str, wf_id: str = "wf-1") -> Workflow:
    node = WorkflowNode(
        id="n1", workflow_id=wf_id, name="Fetch", node_type=NodeType.TASK,
        worker_type="http-request", description="d",
    )
    return Workflow(id=wf_id, workflow_id=wf_id, name=name, nodes=[node])


class TestEventEnricherCache:
    def test_n_distinct_workflows_stay_bounded(self):
        repo = MagicMock()
        repo.get_by_id.side_effect = lambda wid: _workflow(f"name-{wid}", wid)
        cache = BoundedTTLCache(maxsize=100, ttl_seconds=30, name="m4")
        enricher = CentralizedEventEnricher(workflow_repo=repo, workflow_cache=cache)

        for i in range(5_000):
            enricher._get_workflow(f"wf-{i}")

        assert cache.size() <= 100, (
            f"{cache.size()} entries after 5000 distinct workflows — unbounded"
        )

    def test_an_edited_workflow_becomes_visible_within_the_ttl(self):
        """The correctness half of M4 — a stale read that lasted the pod's life."""
        clock = _FakeClock()
        repo = MagicMock()
        repo.get_by_id.return_value = _workflow("Original Name")
        enricher = CentralizedEventEnricher(
            workflow_repo=repo,
            workflow_cache=BoundedTTLCache(maxsize=64, ttl_seconds=30, clock=clock),
        )

        _, meta = enricher._get_workflow("wf-1")
        assert meta["name"] == "Original Name"

        # Someone edits the workflow.
        repo.get_by_id.return_value = _workflow("Renamed")

        _, meta = enricher._get_workflow("wf-1")
        assert meta["name"] == "Original Name", "within the TTL the cache is fine"

        clock.advance(31)
        _, meta = enricher._get_workflow("wf-1")
        assert meta["name"] == "Renamed", (
            "the edit is still invisible after the TTL — this pod will serve "
            "the pre-edit workflow until it restarts"
        )

    def test_within_the_ttl_the_repo_is_hit_once(self):
        repo = MagicMock()
        repo.get_by_id.return_value = _workflow("N")
        enricher = CentralizedEventEnricher(
            workflow_repo=repo,
            workflow_cache=BoundedTTLCache(maxsize=64, ttl_seconds=30),
        )
        for _ in range(100):
            enricher._get_workflow("wf-1")
        assert repo.get_by_id.call_count == 1

    def test_invalidate_forces_a_refetch(self):
        repo = MagicMock()
        repo.get_by_id.return_value = _workflow("N")
        enricher = CentralizedEventEnricher(
            workflow_repo=repo,
            workflow_cache=BoundedTTLCache(maxsize=64, ttl_seconds=3600),
        )
        enricher._get_workflow("wf-1")
        assert enricher.invalidate_workflow("wf-1") is True
        enricher._get_workflow("wf-1")
        assert repo.get_by_id.call_count == 2
        assert enricher.invalidate_workflow("absent") is False

    def test_a_repo_failure_is_not_cached(self):
        """Best-effort enrichment must not be blinded until a TTL expires."""
        repo = MagicMock()
        repo.get_by_id.side_effect = RuntimeError("db down")
        enricher = CentralizedEventEnricher(
            workflow_repo=repo,
            workflow_cache=BoundedTTLCache(maxsize=64, ttl_seconds=3600),
        )
        assert enricher._get_workflow("wf-1") is None
        repo.get_by_id.side_effect = None
        repo.get_by_id.return_value = _workflow("Recovered")
        _, meta = enricher._get_workflow("wf-1")
        assert meta["name"] == "Recovered", "a transient failure was cached"

    def test_a_workflow_that_does_not_exist_is_not_cached(self):
        repo = MagicMock()
        repo.get_by_id.return_value = None
        enricher = CentralizedEventEnricher(
            workflow_repo=repo,
            workflow_cache=BoundedTTLCache(maxsize=64, ttl_seconds=3600),
        )
        assert enricher._get_workflow("ghost") is None
        assert enricher.cache_stats().size == 0

    def test_cache_stats_are_exposed(self):
        enricher = CentralizedEventEnricher(
            workflow_repo=MagicMock(),
            workflow_cache=BoundedTTLCache(
                maxsize=16, ttl_seconds=30, name="event_enricher_workflow"),
        )
        assert enricher.cache_stats().name == "event_enricher_workflow"
        assert enricher.cache_stats().maxsize == 16
