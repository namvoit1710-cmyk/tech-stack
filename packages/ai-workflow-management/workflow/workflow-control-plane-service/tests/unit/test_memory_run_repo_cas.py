"""Phase M.2.1 CAS contract — MemoryRunRepository.

Mirrors the ``IRunRepository.save`` docstring:
- First insert is unconditional; sets ``run.version = 1``.
- Matching-version save increments ``run.version`` in place.
- Stale-version save raises ``OptimisticConcurrencyError``.
- Same contract holds across repeated saves without reload.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.repositories.run_repository_interface import (
    OptimisticConcurrencyError,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import (
    MemoryRunRepository,
)


def _make_run(run_id: str = "r-1", **overrides) -> WorkflowRun:
    defaults = dict(
        id=run_id, workflow_id="wf-1", status=RunStatus.RUNNING,
    )
    defaults.update(overrides)
    return WorkflowRun(**defaults)


class TestFirstInsert:
    def test_first_save_is_unconditional(self):
        repo = MemoryRunRepository()
        run = _make_run(version=0)
        repo.save(run)
        assert run.version == 1
        assert repo.get_by_id("r-1").version == 1

    def test_first_save_ignores_caller_supplied_version(self):
        """Caller may have constructed the run with version=42 (e.g.
        replayed from some external source). First insert still
        lands at 1 — no CAS check because nothing to compare against.
        """
        repo = MemoryRunRepository()
        run = _make_run(version=42)
        repo.save(run)
        assert run.version == 1


class TestMatchingVersion:
    def test_save_increments_version_in_place(self):
        repo = MemoryRunRepository()
        run = _make_run()
        repo.save(run)  # → 1
        repo.save(run)  # → 2
        repo.save(run)  # → 3
        assert run.version == 3
        assert repo.get_by_id("r-1").version == 3

    def test_load_mutate_save_cycle(self):
        repo = MemoryRunRepository()
        initial = _make_run()
        repo.save(initial)  # persisted at v1

        reloaded = repo.get_by_id("r-1")
        reloaded.status = RunStatus.PAUSED
        repo.save(reloaded)
        assert reloaded.version == 2
        assert repo.get_by_id("r-1").status == RunStatus.PAUSED


class TestStaleVersionConflict:
    def test_save_with_stale_version_raises(self):
        """Two independent callers each hold their own ``WorkflowRun``
        for the same ``id``. Caller B's save wins; caller A's save
        raises because A's ``version`` is now stale."""
        repo = MemoryRunRepository()

        # Caller A loads + prepares a mutation.
        caller_a = _make_run()
        # Caller B loads the same initial state independently.
        caller_b = _make_run()

        # Caller B saves first, bumping persisted version to 1.
        repo.save(caller_b)
        assert caller_b.version == 1
        # Caller B mutates again + saves → v2.
        caller_b.status = RunStatus.PAUSED
        repo.save(caller_b)
        assert caller_b.version == 2

        # Caller A still holds version=0 (pre-save) — attempts to save.
        caller_a.status = RunStatus.CANCELLED
        with pytest.raises(OptimisticConcurrencyError) as excinfo:
            repo.save(caller_a)
        assert excinfo.value.run_id == "r-1"
        assert excinfo.value.expected == 0
        assert excinfo.value.actual == 2

    def test_conflict_does_not_mutate_store(self):
        repo = MemoryRunRepository()
        winner = _make_run()
        repo.save(winner)
        winner.status = RunStatus.COMPLETED
        repo.save(winner)  # v2

        loser = _make_run(version=1, status=RunStatus.CANCELLED)
        with pytest.raises(OptimisticConcurrencyError):
            repo.save(loser)
        # Store still reflects the winner's write.
        stored = repo.get_by_id("r-1")
        assert stored.status == RunStatus.COMPLETED
        assert stored.version == 2

    def test_mismatch_with_actual_less_than_expected(self):
        """Defensive case: caller hands us version=5 but nothing is
        stored yet. Treated as first insert (unconditional)."""
        repo = MemoryRunRepository()
        run = _make_run(version=5)
        repo.save(run)
        assert run.version == 1
