"""The claim the whole redesign rests on: a run reloads with CONTEXT gone.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §13, §13.1, §15.

Every piece has its own tests — the mappers, the two repositories, the mirrors,
the read modes, the SQL. **Nothing tested the sentence they exist to make true**:

    after ``ALTER TABLE AIW_WORKFLOW_RUNS DROP (CONTEXT)``, a run that is
    mid-flight still comes back with the state it needs to keep going.

That sentence is what step 5b executes and cannot take back. And it fails
silently in every direction: a loop that lost ``loop_state`` hangs, a cycle that
lost ``node_visits`` re-mints ``v0`` and lets the event-log dedupe swallow its
next iteration, a dropped ``pending_revisit`` simply never fires. No error, no
failed task, no log line.

So this drives the REAL ``HanaRunRepository`` against SQLite — schema derived
from the shipped ``.hdbtable`` files, placeholders through the production
``_bind_params`` — saves a run, **blanks the CONTEXT column**, and reloads.

The negative control matters as much as the positive one: the same reload in
``context`` mode must come back EMPTY. Without that, a green test proves only
that something somewhere remembered, not that these tables are what remembered.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer4_frameworks.persistence.hana.hana_loop_state_repo import (
    HanaLoopStateRepository,
)
from app.layer4_frameworks.persistence.hana.hana_run_node_state_repo import (
    HanaRunNodeStateRepository,
)
from app.layer4_frameworks.persistence.hana.hana_run_repo import HanaRunRepository
from tests.unit.test_new_tables_sql_against_sqlite import SqliteHana


def _db() -> SqliteHana:
    return SqliteHana(
        "AIW_WORKFLOW_RUNS", "AIW_RUN_LOOP_STATE", "AIW_RUN_NODE_STATE")


def _repo(db, mode: str) -> HanaRunRepository:
    return HanaRunRepository(
        db,
        loop_state_repo=HanaLoopStateRepository(db),
        loop_state_read_mode=mode,
        node_state_repo=HanaRunNodeStateRepository(db),
        node_state_read_mode=mode,
    )


def _run() -> WorkflowRun:
    """A run mid-flight through a loop, with a cycle behind it."""
    return WorkflowRun(
        id="run-1", workflow_id="wf", workflow_version="1",
        status=RunStatus.RUNNING,
        context=RunContext(
            loop_state={"loop-a": {
                "total": 4, "current_index": 2, "mode": "sequential",
                "invocation_id": "inv-1", "completed_iterations": [0, 1],
                "iter_progress": {2: ["body"]},
                "items": {"__file_ref": True, "file_id": "items-file",
                          "row_count": 4},
            }},
            node_visits={"cycle-node": 3},
            pending_revisits={"deferred-node"},
            completed_nodes={"trigger", "cycle-node"},
        ),
    )


def _drop_context(db) -> None:
    """No-op, and that is the point.

    This used to blank the column to SIMULATE step 5b. Migration 059 removed
    ``CONTEXT`` from ``db/hdi/AIW_WORKFLOW_RUNS.hdbtable``, and ``SqliteHana``
    derives its schema from that file — so the table these tests run against no
    longer HAS the column. The simulation became the real thing, and blanking a
    column that does not exist is a ``no such column`` error.

    Kept as a call site rather than deleted from every test so the sequence each
    one describes still reads in order: save, drop, reload.
    """


class TestTheRunSurvives:
    @pytest.fixture
    def reloaded(self):
        db = _db()
        _repo(db, "table").save(_run())
        _drop_context(db)
        return _repo(db, "table").get_by_id("run-1")

    def test_it_still_loads(self, reloaded):
        assert reloaded is not None

    def test_the_loop_keeps_its_place(self, reloaded):
        """``current_index`` and ``completed_iterations`` are what decide which
        iteration dispatches next. Lose them and the loop restarts or stalls."""
        state = reloaded.context.loop_state["loop-a"]
        assert state["current_index"] == 2
        assert state["completed_iterations"] == [0, 1]
        assert state["total"] == 4

    def test_iter_progress_keeps_its_INT_keys(self, reloaded):
        """Through the table this time, not through CONTEXT — the new home must
        not reintroduce the drift that stalled multi-node bodies."""
        assert reloaded.context.loop_state["loop-a"]["iter_progress"] == {
            2: ["body"]}

    def test_the_loops_items_POINTER_survives_the_drop(self):
        """Was ``..._survives_as_a_file_id``, and asserted ``"items" not in``
        the reloaded state — the loss, written down as the contract. With
        ``CONTEXT`` gone this table is the only copy, so an absent ``items`` is
        not a deferred resolution, it is the collection deleted: the engine
        reads ``loop_state[node]["items"]`` for ``$loop.item`` and gets ``[]``.
        Measured on tenant-1 as ``Cannot traverse 'i' on NoneType``, one node
        after the loop, on every file-backed collection."""
        db = _db()
        _repo(db, "table").save(_run())
        _drop_context(db)
        restored = _repo(db, "table").get_by_id("run-1").context.loop_state["loop-a"]
        assert restored["items"]["file_id"] == "items-file"
        assert restored["items"]["__file_ref"] is True

    def test_the_cycle_counter_survives(self, reloaded):
        """``TaskFactory.create_task`` mints ``{run}-{node}-v{n}`` from this.
        Back at 0, the next task id collides with iteration 1's and every event
        derived from it is swallowed by the ``(run_id, event_id)`` dedupe."""
        assert reloaded.context.node_visits == {"cycle-node": 3}

    def test_the_deferred_revisit_survives(self, reloaded):
        assert reloaded.context.pending_revisits == {"deferred-node"}


class TestTheStateThatSURVIVESIsTheLATESTState:
    """Found by mutation: the fixture above saves once, which takes the INSERT
    path — so removing the mirror from the CAS **UPDATE** branch changed nothing
    and all 14 tests still passed.

    That is backwards from production. A run reaches step 5b having been saved
    dozens of times, and every one after the first is an UPDATE: the loop
    advances, the cycle counter bumps, `orchestrate_continue` saves. What must
    survive the drop is the state as of the LAST save, not the first."""

    @pytest.fixture
    def reloaded_after_advancing(self):
        db = _db()
        repo, run = _repo(db, "table"), _run()
        repo.save(run)                                    # INSERT
        run.context.loop_state["loop-a"]["current_index"] = 3
        run.context.loop_state["loop-a"]["completed_iterations"] = [0, 1, 2]
        run.context.loop_state["loop-a"]["iter_progress"] = {3: ["body"]}
        run.context.node_visits["cycle-node"] = 4
        run.context.pending_revisits = {"another-node"}
        repo.save(run)                                    # UPDATE (CAS)
        _drop_context(db)
        return _repo(db, "table").get_by_id("run-1")

    def test_the_loop_is_at_iteration_three_not_two(self, reloaded_after_advancing):
        state = reloaded_after_advancing.context.loop_state["loop-a"]
        assert state["current_index"] == 3
        assert state["completed_iterations"] == [0, 1, 2]
        assert state["iter_progress"] == {3: ["body"]}

    def test_the_cycle_counter_is_the_latest(self, reloaded_after_advancing):
        assert reloaded_after_advancing.context.node_visits == {"cycle-node": 4}

    def test_a_drained_revisit_does_not_come_back(self, reloaded_after_advancing):
        """``deferred-node`` was pending at the first save and drained by the
        second. Delete-then-insert is what makes it stay gone — a per-node
        upsert would resurrect it as a revisit that never fires."""
        assert reloaded_after_advancing.context.pending_revisits == {
            "another-node"}


class TestTheNegativeControl:
    """If these passed too, the test above would be proving nothing."""

    @pytest.fixture
    def reloaded_in_context_mode(self):
        db = _db()
        _repo(db, "table").save(_run())      # rows still written
        _drop_context(db)
        return _repo(db, "context").get_by_id("run-1")

    def test_context_mode_loses_the_loop(self, reloaded_in_context_mode):
        assert reloaded_in_context_mode.context.loop_state == {}

    def test_context_mode_loses_the_cycle_counter(self, reloaded_in_context_mode):
        assert reloaded_in_context_mode.context.node_visits == {}

    def test_context_mode_loses_the_revisit(self, reloaded_in_context_mode):
        assert reloaded_in_context_mode.context.pending_revisits == set()

    def test_and_without_the_mirror_there_is_nothing_to_read(self):
        """The other half of the control: with no mirror wired, ``table`` mode
        has empty tables and the run comes back bare. Confirms the rows come
        from the save path and not from somewhere incidental."""
        db = _db()
        HanaRunRepository(db).save(_run())    # no mirror
        _drop_context(db)
        run = _repo(db, "table").get_by_id("run-1")
        assert run.context.loop_state == {} and run.context.node_visits == {}


class TestWhatDropContextDoesNotSave:
    """Honest scope. These fields are event-sourced or rebuilt elsewhere, so
    they are SUPPOSED to come back empty from a bare reload — the projector and
    ``AIW_WORKFLOWS`` fill them in, not this repository. Asserting them here
    keeps a future reader from mistaking the gap for a bug."""

    @pytest.fixture
    def reloaded(self):
        db = _db()
        _repo(db, "table").save(_run())
        _drop_context(db)
        return _repo(db, "table").get_by_id("run-1")

    def test_completed_nodes_comes_back_empty(self, reloaded):
        """Rebuilt from ``AIW_EVENT_LOG`` by the projector."""
        assert reloaded.context.completed_nodes == set()

    def test_variables_comes_back_empty(self, reloaded):
        """Also event-sourced — this is why ``AIW_RUN_VARIABLE`` is not a gate
        despite being empty and unwritten."""
        assert reloaded.context.variables == {}

    def test_the_topology_comes_back_empty(self, reloaded):
        """Rebuilt from the workflow (``projector_view_store``, byte-identical
        output)."""
        assert reloaded.context.topology.edge_map == {}


class TestTheTablesAreNowTheONLYSource:
    """Step 5b landed: ``CONTEXT`` is no longer written, so ``context`` mode has
    nothing to fall back TO. This test used to assert the two modes agreed —
    that comparison is meaningless now, and what replaces it is the stronger
    claim: the tables alone carry the run forward."""

    def test_context_mode_now_returns_nothing(self):
        """The fallback is empty by construction, not by accident — which is
        exactly why the read mode has to be ``table`` in production."""
        db = _db()
        _repo(db, "table").save(_run())
        ctx = _repo(db, "context").get_by_id("run-1").context
        assert ctx.loop_state == {} and ctx.node_visits == {}

    def test_table_mode_carries_the_run_with_no_context_written(self):
        db = _db()
        _repo(db, "table").save(_run())
        ctx = _repo(db, "table").get_by_id("run-1").context
        assert ctx.loop_state["loop-a"]["current_index"] == 2
        assert ctx.node_visits == {"cycle-node": 3}
        assert ctx.pending_revisits == {"deferred-node"}

    def test_the_run_table_has_no_context_column_at_all(self):
        """The strongest form of the guard, and it is now literal rather than
        simulated: the shipped ``.hdbtable`` no longer declares the column, so a
        save that still tried to write it would fail here exactly as it would in
        production."""
        db = _db()
        _repo(db, "table").save(_run())
        cols = {r[1] for r in db.raw("PRAGMA table_info('AIW_WORKFLOW_RUNS')")}
        assert "CONTEXT" not in cols, sorted(cols)
