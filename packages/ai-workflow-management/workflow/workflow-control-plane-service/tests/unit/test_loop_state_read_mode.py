"""Where a loaded run's ``loop_state`` comes from — ``LOOP_STATE_READ_MODE``.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §15.8. Staged exactly
like the node-output read (``NODE_OUTPUT_READ_MODE``) and for the same reason:
flipping a read path blind is how a silently-wrong value reaches production.

The stakes are higher here than for node output. A run that loads with the wrong
``loop_state`` does not raise — the loop stalls at an iteration boundary, or a
``BREAK`` lands on the wrong loop. Both are silent, and the population that would
notice is 23 loops in the history of this tenant.
"""
from __future__ import annotations

import json

import pytest

from app.layer4_frameworks.persistence.hana.hana_run_repo import (
    HanaRunRepository,
    _loop_state_differences,
)


class FakeDb:
    def __init__(self, context: dict | None = None) -> None:
        self.context = context if context is not None else {}
        self.queries: list[str] = []

    def execute_query(self, sql: str, params: tuple = ()) -> list[dict]:
        self.queries.append(sql)
        return [{"id": "run-1", "workflow_id": "wf", "workflow_version": "1",
                 "status": "RUNNING", "context": json.dumps(self.context)}]

    def execute_write(self, sql: str, params: tuple = ()) -> int:
        return 1


class SpyLoopStateRepo:
    def __init__(self, rows: dict | None = None, explode: bool = False) -> None:
        self.rows = rows if rows is not None else {}
        self.reads: list[str] = []
        self._explode = explode

    def get_run(self, run_id: str) -> dict:
        self.reads.append(run_id)
        if self._explode:
            raise RuntimeError("HANA said no")
        return self.rows


def _ctx(loop_state: dict) -> dict:
    return {"variables": {}, "loop_state": loop_state, "completed_nodes": []}


def _repo(mode: str, context_state: dict, table_state: dict, **kw):
    db = FakeDb(_ctx(context_state))
    spy = SpyLoopStateRepo(table_state, **kw)
    return HanaRunRepository(db, loop_state_repo=spy,
                             loop_state_read_mode=mode), db, spy


CONTEXT_STATE = {"l1": {"total": 4, "current_index": 1}}
TABLE_STATE = {"l1": {"total": 4, "current_index": 3}}


class TestContextModeIsTheDefault:
    def test_it_serves_context(self):
        repo, db, spy = _repo("context", CONTEXT_STATE, TABLE_STATE)
        assert repo.get_by_id("run-1").context.loop_state == CONTEXT_STATE

    def test_it_never_queries_the_table(self):
        """The default must cost exactly nothing until someone opts in."""
        repo, db, spy = _repo("context", CONTEXT_STATE, TABLE_STATE)
        repo.get_by_id("run-1")
        assert spy.reads == []

    def test_the_constructor_default_is_context(self):
        db = FakeDb(_ctx(CONTEXT_STATE))
        spy = SpyLoopStateRepo(TABLE_STATE)
        assert HanaRunRepository(db, loop_state_repo=spy).get_by_id(
            "run-1").context.loop_state == CONTEXT_STATE

    @pytest.mark.parametrize("typo", ["Table", "on", "true", "", "tabel", None])
    def test_an_unrecognised_mode_is_treated_as_context(self, typo):
        """A typo in an env var must not silently flip a read path — the same
        fail-safe the node-output read needed after a test caught it going the
        other way."""
        repo, db, spy = _repo(typo, CONTEXT_STATE, TABLE_STATE)
        assert repo.get_by_id("run-1").context.loop_state == CONTEXT_STATE
        assert spy.reads == []


class TestShadowMode:
    def test_it_still_serves_context(self):
        repo, db, spy = _repo("shadow", CONTEXT_STATE, TABLE_STATE)
        assert repo.get_by_id("run-1").context.loop_state == CONTEXT_STATE

    def test_it_reads_the_table_too(self):
        repo, db, spy = _repo("shadow", CONTEXT_STATE, TABLE_STATE)
        repo.get_by_id("run-1")
        assert spy.reads == ["run-1"]

    def test_a_divergence_is_logged(self, caplog):
        repo, db, spy = _repo("shadow", CONTEXT_STATE, TABLE_STATE)
        with caplog.at_level("WARNING"):
            repo.get_by_id("run-1")
        assert any("loop_state divergence" in r.message for r in caplog.records)

    def test_agreement_is_silent(self, caplog):
        repo, db, spy = _repo("shadow", CONTEXT_STATE, CONTEXT_STATE)
        with caplog.at_level("WARNING"):
            repo.get_by_id("run-1")
        assert not any("divergence" in r.message for r in caplog.records)

    def test_a_run_with_no_loops_never_queries(self):
        """6,105 of 6,116 runs have no loop_state. Shadow mode is for collecting
        evidence, not for adding a round-trip to every run load."""
        repo, db, spy = _repo("shadow", {}, TABLE_STATE)
        repo.get_by_id("run-1")
        assert spy.reads == []


class TestTableMode:
    def test_it_serves_the_table(self):
        repo, db, spy = _repo("table", CONTEXT_STATE, TABLE_STATE)
        assert repo.get_by_id("run-1").context.loop_state == TABLE_STATE

    def test_it_falls_back_to_context_when_the_table_is_empty(self):
        """Runs written before the mirror existed have no rows. Serving ``{}``
        for those would silently un-start every loop mid-flight."""
        repo, db, spy = _repo("table", CONTEXT_STATE, {})
        assert repo.get_by_id("run-1").context.loop_state == CONTEXT_STATE

    def test_it_queries_even_when_context_is_empty(self):
        """The gate shadow mode uses cannot apply here — after DROP CONTEXT
        there is nothing left to gate on."""
        repo, db, spy = _repo("table", {}, TABLE_STATE)
        assert repo.get_by_id("run-1").context.loop_state == TABLE_STATE
        assert spy.reads == ["run-1"]


class TestAFailedReadNeverBreaksTheLoad:
    def test_it_serves_context_instead(self):
        repo, db, spy = _repo("table", CONTEXT_STATE, {}, explode=True)
        assert repo.get_by_id("run-1").context.loop_state == CONTEXT_STATE

    def test_it_is_logged(self, caplog):
        repo, db, spy = _repo("shadow", CONTEXT_STATE, {}, explode=True)
        with caplog.at_level("WARNING"):
            repo.get_by_id("run-1")
        assert any("loop_state read failed" in r.message for r in caplog.records)

    def test_no_repo_configured_serves_context(self):
        db = FakeDb(_ctx(CONTEXT_STATE))
        repo = HanaRunRepository(db, loop_state_read_mode="table")
        assert repo.get_by_id("run-1").context.loop_state == CONTEXT_STATE


class TestListPathsDoNotPayForIt:
    """``_row_to_entity`` runs per ROW. ``list_by_workflow`` and
    ``get_by_root_id`` feed list / aggregate views that never touch the loop
    machinery — every loop handler operates on a run loaded by id."""

    def test_list_by_workflow_never_queries_the_table(self):
        repo, db, spy = _repo("table", CONTEXT_STATE, TABLE_STATE)
        repo.list_by_workflow("wf")
        assert spy.reads == []

    def test_get_by_root_id_never_queries_the_table(self):
        repo, db, spy = _repo("table", CONTEXT_STATE, TABLE_STATE)
        repo.get_by_root_id("root-1")
        assert spy.reads == []

    def test_get_by_id_version_does(self):
        repo, db, spy = _repo("table", CONTEXT_STATE, TABLE_STATE)
        assert repo.get_by_id_version("run-1", "1").context.loop_state == TABLE_STATE


class TestTheComparisonIgnoresWhatTheTableCannotReturn:
    """``from_row`` leaves a ref-backed payload key ABSENT — it has no
    file-service client and will not invent a value. Comparing it anyway would
    fire on every healthy loop, which is the fastest way to make a shadow signal
    worthless."""

    REF = {"__file_ref": True, "file_id": "f1"}

    def test_a_ref_backed_items_is_not_a_divergence(self):
        ctx = {"l1": {"total": 4, "items": self.REF}}
        tbl = {"l1": {"total": 4}}
        assert _loop_state_differences(ctx, tbl) == []

    def test_a_ref_backed_iteration_results_is_not_either(self):
        ctx = {"l1": {"total": 4, "iteration_results": self.REF}}
        tbl = {"l1": {"total": 4}}
        assert _loop_state_differences(ctx, tbl) == []

    def test_an_INLINE_items_that_went_missing_IS_a_divergence(self):
        """Inline values are stored on the row, so their absence is real."""
        ctx = {"l1": {"total": 4, "items": [{"a": 1}]}}
        tbl = {"l1": {"total": 4}}
        assert _loop_state_differences(ctx, tbl) == ["l1.items"]

    def test_it_NAMES_the_key_that_differs(self):
        """The reason this returns a list rather than a bool. The first live
        shadow run logged 22 divergences that all read
        ``context=['loop'] table=['loop']`` — same key set every time, so a
        genuine mapper bug and a benign read-your-writes race were
        indistinguishable. A signal that does not say what fired is not a
        signal."""
        assert _loop_state_differences(CONTEXT_STATE, TABLE_STATE) == [
            "l1.current_index"]

    def test_several_differing_keys_are_all_named(self):
        ctx = {"l1": {"total": 4, "mode": "sequential", "current_index": 1}}
        tbl = {"l1": {"total": 9, "mode": "parallel", "current_index": 1}}
        assert _loop_state_differences(ctx, tbl) == ["l1.mode", "l1.total"]

    def test_a_missing_loop_node_is_named_by_side(self):
        assert _loop_state_differences({"l1": {}, "l2": {}}, {"l1": {}}) == [
            "l2:only-in-context"]
        assert _loop_state_differences({"l1": {}}, {"l1": {}, "l2": {}}) == [
            "l2:only-in-table"]

    def test_an_extra_key_on_either_side_is_a_divergence(self):
        assert _loop_state_differences(
            {"l1": {"total": 1}}, {"l1": {"total": 1, "extra": 2}}) == [
            "l1.extra"]

    def test_identical_states_agree(self):
        assert _loop_state_differences(CONTEXT_STATE, dict(CONTEXT_STATE)) == []

    def test_two_empties_agree(self):
        assert _loop_state_differences({}, {}) == []

    def test_a_non_dict_entry_is_compared_directly(self):
        assert _loop_state_differences({"l1": "x"}, {"l1": "x"}) == []
        assert _loop_state_differences({"l1": "x"}, {"l1": "y"}) != []
