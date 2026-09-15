"""``HanaRunRepository.save`` mirrors ``context.loop_state`` into its own table.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §13, §15. Table from
migrations 056 / 057, live on tenant-1 (``SEQ`` verified in ``TABLE_COLUMNS``).

``save`` is the ONE place ``loop_state`` reaches the database. Five sites mutate
it — the two loop drivers, the engine, and the LOOP_EXIT / LOOP_CONTINUE
handlers — and every one relies on this save to persist the result. So the mirror
goes here, for the same reason ``normalize_loop_state_keys`` went into
``RunContext.__post_init__`` rather than into each deserialize site.

It is a SHADOW write: nothing reads the table yet. A shadow write that can fail a
run is worse than no shadow write at all.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer4_frameworks.persistence.hana.hana_run_repo import HanaRunRepository


class FakeDb:
    """Enough of ``IDatabaseConnection`` for the CAS save path."""

    def __init__(self) -> None:
        self.version: int | None = None
        self.writes: list[str] = []

    def execute_query(self, sql: str, params: tuple = ()) -> list[dict]:
        if "VERSION" in sql.upper() and "SELECT" in sql.upper():
            return [{"version": self.version}] if self.version is not None else []
        return []

    def execute_write(self, sql: str, params: tuple = ()) -> int:
        self.writes.append(sql)
        if sql.strip().upper().startswith("INSERT"):
            self.version = 1
        return 1

    def execute_write_returning_rowcount(self, sql: str, params: tuple = ()) -> int:
        self.writes.append(sql)
        self.version = (self.version or 0) + 1
        return 1


class SpyLoopStateRepo:
    def __init__(self, explode: bool = False) -> None:
        self.saved: list[tuple[str, dict]] = []
        self.deleted: list[str] = []
        self._explode = explode

    def save_run(self, run_id: str, loop_state: dict) -> int:
        if self._explode:
            raise RuntimeError("HANA said no")
        self.saved.append((run_id, dict(loop_state)))
        return len(loop_state)

    def delete_run(self, run_id: str) -> None:
        self.deleted.append(run_id)


def _run(loop_state: dict | None = None) -> WorkflowRun:
    return WorkflowRun(
        id="run-1", workflow_id="wf", workflow_version="1",
        status=RunStatus.RUNNING,
        context=RunContext(loop_state=loop_state or {}),
    )


def _loop() -> dict:
    return {"l1": {"total": 2, "invocation_id": "inv",
                   "items": {"__file_ref": True, "file_id": "f1"}}}


@pytest.fixture
def wired():
    db, spy = FakeDb(), SpyLoopStateRepo()
    return HanaRunRepository(db, loop_state_repo=spy), db, spy


class TestItMirrorsOnBothSavePaths:
    def test_on_the_first_insert(self, wired):
        repo, db, spy = wired
        repo.save(_run(_loop()))
        assert [rid for rid, _ in spy.saved] == ["run-1"]

    def test_on_the_cas_update(self, wired):
        """A loop mutates its state on every iteration, so essentially all of a
        loop's writes go down the UPDATE path — mirroring only on INSERT would
        capture the state the loop STARTED with and nothing after."""
        repo, db, spy = wired
        run = _run(_loop())
        repo.save(run)
        run.context.loop_state["l1"]["total"] = 9
        repo.save(run)
        assert len(spy.saved) == 2
        assert spy.saved[-1][1]["l1"]["total"] == 9

    def test_it_passes_the_whole_dict(self, wired):
        repo, db, spy = wired
        state = {"outer": {"total": 1}, "inner": {"total": 2}}
        repo.save(_run(state))
        assert list(spy.saved[0][1]) == ["outer", "inner"]


class TestItStaysOffTheHotPath:
    def test_a_run_with_no_loops_never_touches_the_table(self, wired):
        """6,105 of 6,116 runs on tenant-1 have no loop_state. This redesign
        exists to remove round-trips from the save path — an unconditional
        DELETE here would add 43,199 statements to buy nothing."""
        repo, db, spy = wired
        repo.save(_run())
        assert spy.saved == [] and spy.deleted == []

    def test_no_repo_configured_is_a_no_op(self):
        """Memory / sqlite backends and every existing test construct
        ``HanaRunRepository(db)`` with one argument."""
        db = FakeDb()
        HanaRunRepository(db).save(_run(_loop()))   # must not raise

    def test_the_run_is_still_saved(self, wired):
        repo, db, spy = wired
        run = _run(_loop())
        repo.save(run)
        assert run.version == 1
        assert any(w.strip().upper().startswith("INSERT") for w in db.writes)


class TestAFailedMirrorNeverFailsTheRun:
    """Same contract as ``_record_node_output``. Nothing reads this table yet,
    so a mirror that can take a run down is strictly negative value."""

    def test_the_save_still_succeeds(self):
        db, spy = FakeDb(), SpyLoopStateRepo(explode=True)
        repo = HanaRunRepository(db, loop_state_repo=spy)
        run = _run(_loop())
        repo.save(run)                        # must not raise
        assert run.version == 1

    def test_it_is_logged(self, caplog):
        db, spy = FakeDb(), SpyLoopStateRepo(explode=True)
        repo = HanaRunRepository(db, loop_state_repo=spy)
        with caplog.at_level("WARNING"):
            repo.save(_run(_loop()))
        assert any("loop_state mirror failed" in r.message for r in caplog.records)

    def test_the_version_still_advances_on_update(self):
        """A swallowed exception must not leave the aggregate thinking its save
        did not happen — the next CAS would then fail with a version mismatch."""
        db, spy = FakeDb(), SpyLoopStateRepo(explode=True)
        repo = HanaRunRepository(db, loop_state_repo=spy)
        run = _run(_loop())
        repo.save(run)
        repo.save(run)
        assert run.version == 2


class TestTheClearPathsDropTheirRows:
    """The mirror is gated on a NON-EMPTY dict, so ``loop_state.clear()`` cannot
    propagate through it. There are exactly two callers that clear it, and a row
    left behind reads as a loop that is still running."""

    def test_reset_test_run_deletes_them(self):
        from app.layer2_application.features.reset_test_run.use_cases.reset_test_run_use_case import (  # noqa: E501
            ResetTestRunUseCase,
        )
        spy = SpyLoopStateRepo()
        uc = ResetTestRunUseCase(
            logger=_NullLogger(), workflow_repo=None, run_repo=None,
            loop_state_repo=spy,
        )
        uc._clear_loop_state_rows("run-9")
        assert spy.deleted == ["run-9"]

    def test_a_failure_there_does_not_block_the_reset(self):
        from app.layer2_application.features.reset_test_run.use_cases.reset_test_run_use_case import (  # noqa: E501
            ResetTestRunUseCase,
        )
        uc = ResetTestRunUseCase(
            logger=_NullLogger(), workflow_repo=None, run_repo=None,
            loop_state_repo=SpyLoopStateRepo(explode=True),
        )
        uc._clear_loop_state_rows("run-9")      # must not raise

    def test_no_repo_configured_is_a_no_op(self):
        from app.layer2_application.features.reset_test_run.use_cases.reset_test_run_use_case import (  # noqa: E501
            ResetTestRunUseCase,
        )
        uc = ResetTestRunUseCase(
            logger=_NullLogger(), workflow_repo=None, run_repo=None)
        uc._clear_loop_state_rows("run-9")      # must not raise


class _NullLogger:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass
