"""M1 acceptance (SA-2258): a terminal run must release everything it held.

Three dicts in ``CachedProjectorRunContextViewStore`` — ``_cache``,
``_snap_watermark``, ``_snap_history`` — were never released in production.
``invalidate()`` existed but nothing in ``app/`` called it, and even a caller
doing the right thing only dropped one of the three.

The reason it stayed invisible matters as much as the leak: run-scoped cleanup
was discovered by a duck-typed ``getattr`` scan over the variable resolver's
private ``_strategies`` list. The projector store is not a resolver strategy,
so the scan reached nothing and said nothing. These tests pin the replacement:
an explicit registry a component JOINS, so a future fourth cache is one
``register()`` call rather than a hunt.
"""

from __future__ import annotations

import gc

import pytest

from app.layer1_domain.events import NodeCompleted
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer2_application.interfaces.run_scoped_cache_registry import (
    RunScopedCacheRegistry,
    default_run_scoped_cache_registry,
)
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer4_frameworks.run_context.cached_projector_view_store import (
    CachedProjectorRunContextViewStore,
)


def _envelope(run_id: str, event_id: str, **payload) -> EventLogEnvelope:
    return EventLogEnvelope(
        event_id=event_id,
        run_id=run_id,
        topic="nodecompleted",
        event_type="NodeCompleted",
        timestamp="2026-08-01T00:00:00Z",
        payload={**payload, "run_id": run_id},
    )


def _store(registry: RunScopedCacheRegistry, max_runs: int = 512):
    """A store wired to an ISOLATED registry — never the process-wide one."""
    repo = MemoryEventLogRepository()
    store = CachedProjectorRunContextViewStore(
        repo, cache_registry=registry, max_runs=max_runs,
    )
    return store, repo


def _project(store, repo, run_id: str) -> None:
    repo.append(_envelope(run_id, f"{run_id}-e1", node_id="n1"))
    store.get(run_id)


# ----------------------------------------------------------------------
# The registry
# ----------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.evicted: list[str] = []

    def evict_run(self, run_id: str) -> None:
        self.evicted.append(run_id)


class _Exploder:
    def evict_run(self, run_id: str) -> None:
        raise RuntimeError("boom")


class TestRegistry:
    def test_members_are_swept(self):
        reg = RunScopedCacheRegistry()
        a, b = _Recorder(), _Recorder()
        reg.register(a, "a")
        reg.register(b, "b")
        assert reg.release_run("run-1") == 2
        assert a.evicted == ["run-1"] and b.evicted == ["run-1"]

    def test_one_broken_member_does_not_block_the_others(self):
        """A sweep that aborts halfway turns a partial leak into a total one."""
        reg = RunScopedCacheRegistry()
        good = _Recorder()
        reg.register(_Exploder(), "boom")
        reg.register(good, "good")
        swept = reg.release_run("run-1")
        assert good.evicted == ["run-1"], "the failure stopped the sweep"
        assert swept == 1

    def test_registering_twice_is_a_noop(self):
        reg = RunScopedCacheRegistry()
        rec = _Recorder()
        reg.register(rec)
        reg.register(rec)
        reg.release_run("r")
        assert rec.evicted == ["r"], "the member was swept twice"

    def test_unregister_removes_the_member(self):
        reg = RunScopedCacheRegistry()
        rec = _Recorder()
        reg.register(rec)
        assert reg.unregister(rec) is True
        assert reg.release_run("r") == 0
        assert reg.unregister(rec) is False

    def test_a_member_without_evict_run_is_rejected_loudly(self):
        reg = RunScopedCacheRegistry()
        with pytest.raises(TypeError, match="evict_run"):
            reg.register(object())

    def test_the_registry_is_not_itself_a_leak(self):
        """Membership must not keep a dead store alive."""
        reg = RunScopedCacheRegistry()
        rec = _Recorder()
        reg.register(rec, "temp")
        assert len(reg) == 1
        del rec
        gc.collect()
        assert len(reg) == 0, (
            "the registry pinned its member — the leak just moved one level up"
        )

    def test_releasing_an_unknown_run_is_harmless(self):
        reg = RunScopedCacheRegistry()
        # Hold a strong reference on purpose: membership is weak, so a member
        # nobody else owns is collected immediately. In production the store
        # is owned by the DI container, which is what keeps it alive.
        rec = _Recorder()
        reg.register(rec)
        assert reg.release_run("never-seen") == 1
        assert reg.release_run("") == 0
        assert rec.evicted == ["never-seen"]

    def test_a_member_nobody_else_owns_is_dropped_immediately(self):
        """Documents the weak-membership consequence rather than hiding it."""
        reg = RunScopedCacheRegistry()
        reg.register(_Recorder())   # no strong reference kept anywhere
        gc.collect()
        assert len(reg) == 0
        assert reg.release_run("r") == 0


# ----------------------------------------------------------------------
# The store — the leak itself
# ----------------------------------------------------------------------


class TestTerminalEvictionReleasesAllThree:
    def test_n_runs_to_terminal_return_every_dict_to_baseline(self):
        """The acceptance criterion, stated literally."""
        reg = RunScopedCacheRegistry()
        store, repo = _store(reg)
        baseline = store.cache_sizes()

        for i in range(200):
            _project(store, repo, f"run-{i}")
        grown = store.cache_sizes()
        assert grown["cache"] == 200

        for i in range(200):
            reg.release_run(f"run-{i}")

        after = store.cache_sizes()
        for key in ("cache", "snap_watermark", "snap_history", "lru"):
            assert after[key] == baseline[key], (
                f"{key} did not return to baseline: {after[key]} vs "
                f"{baseline[key]} — this dict is still leaking"
            )

    def test_evict_run_drops_the_snapshot_watermark_and_history_too(self):
        """``invalidate`` dropped only ``_cache``; all three must go."""
        reg = RunScopedCacheRegistry()
        store, repo = _store(reg)
        _project(store, repo, "run-1")
        # Simulate the snapshot bookkeeping the snapshot path would leave.
        store._snap_watermark["run-1"] = (5, 1.0)
        store._snap_history["run-1"] = __import__("collections").deque([5])

        store.evict_run("run-1")

        assert "run-1" not in store._cache
        assert "run-1" not in store._snap_watermark, "watermark survived eviction"
        assert "run-1" not in store._snap_history, "history survived eviction"

    def test_invalidate_now_releases_all_three_as_well(self):
        reg = RunScopedCacheRegistry()
        store, repo = _store(reg)
        _project(store, repo, "run-1")
        store._snap_watermark["run-1"] = (5, 1.0)
        store._snap_history["run-1"] = __import__("collections").deque([5])

        store.invalidate("run-1")

        assert store.cache_sizes()["cache"] == 0
        assert store.cache_sizes()["snap_watermark"] == 0
        assert store.cache_sizes()["snap_history"] == 0

    def test_eviction_is_idempotent(self):
        """A terminal transition can fire more than once."""
        reg = RunScopedCacheRegistry()
        store, repo = _store(reg)
        _project(store, repo, "run-1")
        store.evict_run("run-1")
        store.evict_run("run-1")
        store.evict_run("never-existed")
        store.evict_run("")
        assert store.cache_sizes()["cache"] == 0

    def test_invalidate_all_clears_every_structure(self):
        reg = RunScopedCacheRegistry()
        store, repo = _store(reg)
        for i in range(10):
            _project(store, repo, f"run-{i}")
        store.invalidate_all()
        sizes = store.cache_sizes()
        assert (sizes["cache"], sizes["snap_watermark"],
                sizes["snap_history"], sizes["lru"]) == (0, 0, 0, 0)

    def test_a_still_running_run_is_not_evicted(self):
        """Never-worse: eviction must be terminal-scoped, not indiscriminate."""
        reg = RunScopedCacheRegistry()
        store, repo = _store(reg)
        _project(store, repo, "done-1")
        _project(store, repo, "live-1")
        reg.release_run("done-1")
        assert store.cache_sizes()["cache"] == 1
        assert store.get("live-1") is not None


class TestLruBackstop:
    def test_runs_that_never_reach_terminal_are_still_bounded(self):
        """Crash / cancel-in-flight / lost event — no terminal call ever comes."""
        reg = RunScopedCacheRegistry()
        store, repo = _store(reg, max_runs=50)
        for i in range(1_000):
            _project(store, repo, f"orphan-{i}")
        sizes = store.cache_sizes()
        assert sizes["cache"] <= 50, (
            f"{sizes['cache']} runs resident with no terminal transition — "
            "the backstop is not holding"
        )
        assert sizes["lru"] <= 50
        assert sizes["evictions_overflow"] >= 950

    def test_overflow_eviction_releases_all_three_dicts(self):
        reg = RunScopedCacheRegistry()
        store, repo = _store(reg, max_runs=5)
        for i in range(20):
            _project(store, repo, f"run-{i}")
            store._snap_watermark[f"run-{i}"] = (1, 1.0)
        sizes = store.cache_sizes()
        assert sizes["cache"] <= 5
        assert sizes["snap_watermark"] <= 5, (
            "overflow dropped _cache but left the watermark behind"
        )

    def test_the_backstop_is_not_time_based(self):
        """A TTL here would evict a HOT run and force a cold full-log replay.

        The store takes no ttl argument at all — the only way a live run leaves
        the cache is capacity pressure or its own terminal transition.
        """
        import inspect
        params = inspect.signature(CachedProjectorRunContextViewStore).parameters
        assert not any("ttl" in p.lower() for p in params), (
            f"a TTL knob appeared in the projector cache: {list(params)}"
        )

    def test_recently_used_runs_survive_pressure(self):
        reg = RunScopedCacheRegistry()
        store, repo = _store(reg, max_runs=3)
        for i in range(3):
            _project(store, repo, f"run-{i}")
        store.get("run-0")                       # keep run-0 hot
        _project(store, repo, "run-9")           # evicts the true LRU (run-1)
        assert "run-0" in store._cache
        assert "run-1" not in store._cache


class TestWiring:
    def test_the_store_joins_the_process_wide_registry_by_default(self):
        """Default construction must NOT need a bootstrap change to be swept."""
        registry = default_run_scoped_cache_registry()
        before = len(registry)
        store = CachedProjectorRunContextViewStore(MemoryEventLogRepository())
        try:
            assert len(registry) == before + 1
            assert "CachedProjectorRunContextViewStore" in registry.members()
        finally:
            registry.unregister(store)

    def test_run_scope_manager_release_run_sweeps_the_registry(self):
        from app.layer2_application.orchestration.run_scope_manager import (
            RunScopeManager,
        )
        reg = RunScopedCacheRegistry()
        store, repo = _store(reg)
        _project(store, repo, "run-1")

        mgr = RunScopeManager(run_repo=None, cache_registry=reg)
        assert mgr.release_run("run-1") == 1
        assert store.cache_sizes()["cache"] == 0

    def test_release_run_still_drives_the_legacy_strategy_scan(self):
        """Back-compat: the resolver strategies must not stop being dropped."""
        from app.layer2_application.orchestration.run_scope_manager import (
            RunScopeManager,
        )

        class _Strategy:
            def __init__(self) -> None:
                self.dropped: list[str] = []

            def drop_run_cache(self, run_id: str) -> None:
                self.dropped.append(run_id)

        class _Resolver:
            def __init__(self, strategy) -> None:
                self._strategies = [strategy]

        strategy = _Strategy()
        mgr = RunScopeManager(
            run_repo=None,
            variable_resolver=_Resolver(strategy),
            cache_registry=RunScopedCacheRegistry(),
        )
        mgr.release_run("run-7")
        assert strategy.dropped == ["run-7"]

    def test_the_engine_terminal_paths_call_release_run(self):
        """Both terminal call sites, including the CANCELLED hook."""
        import inspect
        from app.layer2_application.orchestration import orchestration_engine

        source = inspect.getsource(orchestration_engine)
        assert source.count("self._scope.release_run(") >= 2, (
            "a terminal transition still calls only drop_resolver_cache — the "
            "projector store will not be told the run ended"
        )
        assert "self._scope.drop_resolver_cache(" not in source, (
            "a terminal path still uses the resolver-only call"
        )
