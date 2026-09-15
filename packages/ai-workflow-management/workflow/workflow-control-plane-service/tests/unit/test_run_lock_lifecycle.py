"""The per-run asyncio.Lock is released too — SA-2274 (found under SA-2254).

``_run_locks`` was the fifth unbounded per-run structure. ``release_lock``
existed but was called only on the COMPLETED/FAILED path
(``orchestration_engine.py:928``); the CANCELLED path goes through
``drop_run_resolver_cache`` from ``cancel_run_use_case`` and never touched it,
so every cancelled run left one ``asyncio.Lock`` resident for the pod's life.

The fix also closes a latent correctness hole that was already there. The
COMPLETED/FAILED release runs *inside* ``async with self._get_run_lock(run_id)``,
so the old unconditional ``pop`` removed a lock while its own caller still held
it — after which the next caller would build a FRESH lock and could enter the
critical section alongside anyone still waiting on the old object. Releasing a
HELD lock is now refused, and the backstop reclaims it once it is free.
"""

from __future__ import annotations

import asyncio

import pytest

from app.layer2_application.interfaces.cache_budget_registry import (
    CacheBudgetRegistry,
)
from app.layer2_application.orchestration import run_scope_manager as rsm
from app.layer2_application.orchestration.run_scope_manager import RunScopeManager


def _mgr() -> RunScopeManager:
    return RunScopeManager(run_repo=None)


class TestTerminalReleaseCoversEveryPath:
    def test_release_run_frees_the_lock(self):
        """The CANCELLED path reaches release_run, and must free the lock."""
        m = _mgr()
        m.get_lock("run-1")
        assert m.lock_count() == 1
        m.release_run("run-1")
        assert m.lock_count() == 0, (
            "a cancelled run left its asyncio.Lock behind"
        )

    def test_release_lock_is_idempotent(self):
        m = _mgr()
        m.get_lock("run-1")
        assert m.release_lock("run-1") is True
        assert m.release_lock("run-1") is False
        assert m.release_lock("never-existed") is False

    def test_a_thousand_cancelled_runs_leave_nothing(self):
        m = _mgr()
        for i in range(1000):
            m.get_lock(f"run-{i}")
            m.release_run(f"run-{i}")
        assert m.lock_count() == 0


class TestAHeldLockIsNeverDropped:
    """Trading a small leak for a correctness bug is never the right way round."""

    async def test_release_refuses_while_held(self):
        m = _mgr()
        lock = m.get_lock("run-1")
        async with lock:
            assert m.release_lock("run-1") is False
            assert m.lock_count() == 1
        assert m.release_lock("run-1") is True

    async def test_the_same_lock_object_is_returned_while_held(self):
        """If a held lock were dropped, the next caller would get a NEW lock
        and could enter the critical section beside the current holder."""
        m = _mgr()
        first = m.get_lock("run-1")
        async with first:
            m.release_lock("run-1")          # refused
            second = m.get_lock("run-1")
            assert second is first, (
                "a second caller received a different lock object while the "
                "first was inside the critical section"
            )

    async def test_backstop_never_evicts_a_held_lock(self, monkeypatch):
        monkeypatch.setattr(rsm, "_MAX_RUN_LOCKS", 4)
        m = _mgr()
        held = m.get_lock("hot")
        async with held:
            for i in range(50):
                m.get_lock(f"cold-{i}")
            assert m.get_lock("hot") is held, "the held lock was evicted"
            assert m.lock_count() <= 5


class TestTheBackstopBoundsWhatEscapes:
    def test_runs_that_never_terminate_are_bounded(self, monkeypatch):
        """Crash, lost event, cancel-in-flight — no terminal call ever comes."""
        monkeypatch.setattr(rsm, "_MAX_RUN_LOCKS", 32)
        m = _mgr()
        for i in range(5_000):
            m.get_lock(f"orphan-{i}")
        assert m.lock_count() <= 33, (
            f"{m.lock_count()} locks resident with no terminal transition"
        )

    def test_eviction_is_oldest_first(self, monkeypatch):
        monkeypatch.setattr(rsm, "_MAX_RUN_LOCKS", 3)
        m = _mgr()
        for name in ("a", "b", "c"):
            m.get_lock(name)
        m.get_lock("a")            # touch 'a' so it is no longer the oldest
        m.get_lock("d")            # over cap -> evicts the true LRU
        assert "a" in m._run_locks
        assert "b" not in m._run_locks

    def test_cap_is_configurable_and_tolerates_garbage(self, monkeypatch):
        monkeypatch.setenv("MAX_RUN_LOCKS", "7")
        assert rsm._max_run_locks() == 7
        monkeypatch.setenv("MAX_RUN_LOCKS", "loads")
        assert rsm._max_run_locks() == 1024
        monkeypatch.setenv("MAX_RUN_LOCKS", "0")
        assert rsm._max_run_locks() == 1


class TestItReportsItself:
    def test_locks_appear_in_the_memory_budget(self):
        reg = CacheBudgetRegistry()
        m = RunScopeManager(run_repo=None)
        reg.register(m, "run_locks")
        m.get_lock("run-1")
        snap = reg.snapshot()["run_locks"]
        assert snap["entries"] == 1
        assert snap["held"] == 0
        assert snap["maxsize"] == rsm._MAX_RUN_LOCKS

    async def test_held_count_is_reported(self):
        m = _mgr()
        lock = m.get_lock("run-1")
        async with lock:
            assert m.budget_snapshot()["held"] == 1
        assert m.budget_snapshot()["held"] == 0


class TestTheEngineStillReleasesOnEveryTerminalPath:
    def test_both_call_sites_present(self):
        import inspect
        from app.layer2_application.orchestration import orchestration_engine

        src = inspect.getsource(orchestration_engine)
        assert src.count("self._scope.release_run(") >= 2, (
            "a terminal transition no longer routes through release_run"
        )
