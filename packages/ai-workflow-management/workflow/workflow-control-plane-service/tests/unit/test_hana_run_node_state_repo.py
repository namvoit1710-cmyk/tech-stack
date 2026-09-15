"""``AIW_RUN_NODE_STATE`` — the SECOND unbuilt gate on ``DROP CONTEXT``.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §13. Tables from
migrations 055 / 058.

The §13 audit recorded ``node_visits`` and ``pending_revisits`` as *"covered by
migration 055"*. True of the DDL, false of the code: grepping the service for
``AIW_RUN_NODE_STATE`` returned only doc comments, and the table holds **0 rows
on tenant-1**. Neither field appears in ``run_projection.py``, so neither
rebuilds from the event log.

Both fail silently when lost:

* ``node_visits`` feeds ``TaskFactory.create_task``'s ``{run}-{node}-v{n}``
  id (``task_factory.py:77``). Reset it and a cycle's second iteration re-mints
  ``v0``, so every event id derived from that task collides with the first
  iteration's and is swallowed by the ``(run_id, event_id)`` dedupe.
* ``pending_revisits`` is a deferred arrival. Drop it and the node never re-runs.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow_run import RunContext
from app.layer4_frameworks.persistence.hana.hana_run_node_state_repo import (
    HanaRunNodeStateRepository,
)


class FakeDb:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.writes: list[str] = []

    def execute_write(self, sql: str, params: tuple = ()) -> int:
        self.writes.append(sql.split()[0].upper())
        if sql.strip().upper().startswith("DELETE"):
            before = len(self.rows)
            self.rows = [r for r in self.rows if r["RUN_ID"] != params[0]]
            return before - len(self.rows)
        cols = ("RUN_ID", "NODE_ID", "STATE", "VISITS", "PENDING_REVISIT",
                "LAST_OUTPUT_PORT", "UPDATED_AT")
        self.rows.append(dict(zip(cols, params)))
        return 1

    def execute_query(self, sql: str, params: tuple = ()) -> list[dict]:
        return [{k.lower(): v for k, v in r.items()}
                for r in self.rows if r["RUN_ID"] == params[0]]


@pytest.fixture
def repo():
    db = FakeDb()
    return HanaRunNodeStateRepository(db), db


def _ctx(**kw) -> RunContext:
    return RunContext(**kw)


def _row(db, node_id: str) -> dict:
    return next(r for r in db.rows if r["NODE_ID"] == node_id)


class TestTheTwoFieldsThatCannotBeRebuilt:
    def test_visit_counts_are_stored(self, repo):
        r, db = repo
        r.save_run("run-1", _ctx(node_visits={"a": 2, "b": 5}))
        assert _row(db, "a")["VISITS"] == 2 and _row(db, "b")["VISITS"] == 5

    def test_they_come_back(self, repo):
        r, db = repo
        r.save_run("run-1", _ctx(node_visits={"a": 2, "b": 5}))
        assert r.get_run("run-1")["node_visits"] == {"a": 2, "b": 5}

    def test_a_pending_revisit_is_stored_and_returned(self, repo):
        r, db = repo
        r.save_run("run-1", _ctx(pending_revisits={"a"}))
        assert _row(db, "a")["PENDING_REVISIT"] == 1
        assert r.get_run("run-1")["pending_revisits"] == {"a"}

    def test_completed_AND_pending_is_representable(self, repo):
        """The shape that forced migration 058. SA-846 Mode A records a revisit
        for a node that has ALREADY completed, so one ``STATE`` column cannot
        express it — a third state value would erase the completion."""
        r, db = repo
        r.save_run("run-1", _ctx(completed_nodes={"a"}, pending_revisits={"a"}))
        row = _row(db, "a")
        assert row["STATE"] == "completed" and row["PENDING_REVISIT"] == 1
        assert r.get_run("run-1")["pending_revisits"] == {"a"}

    def test_a_zero_visit_count_is_omitted_on_read(self, repo):
        """``node_visits`` is read with ``.get(node_id, 0)`` everywhere, so an
        explicit 0 is the same answer in a bigger dict."""
        r, db = repo
        r.save_run("run-1", _ctx(completed_nodes={"a"}))
        assert r.get_run("run-1")["node_visits"] == {}


class TestWhatItDeliberatelyDoesNotReturn:
    """``completed_nodes``, ``skipped_nodes`` and ``last_output_ports`` all
    rebuild from ``AIW_EVENT_LOG``. They are written ALONGSIDE a node that
    already earned a row — free context for whoever is reading the table to work
    out why a cycle stalled — but handing them back would create a second source
    that eventually disagrees with the projector."""

    def test_state_is_written(self, repo):
        r, db = repo
        r.save_run("run-1", _ctx(node_visits={"a": 1, "b": 1},
                                 completed_nodes={"a"}, skipped_nodes={"b"}))
        assert _row(db, "a")["STATE"] == "completed"
        assert _row(db, "b")["STATE"] == "skipped"

    def test_last_output_port_is_written(self, repo):
        r, db = repo
        r.save_run("run-1", _ctx(node_visits={"a": 1},
                                 last_output_ports={"a": "success"}))
        assert _row(db, "a")["LAST_OUTPUT_PORT"] == "success"

    def test_neither_comes_back(self, repo):
        r, db = repo
        r.save_run("run-1", _ctx(node_visits={"a": 1}, completed_nodes={"a"},
                                 last_output_ports={"a": "success"}))
        assert set(r.get_run("run-1")) == {"node_visits", "pending_revisits"}

    def test_skipped_wins_over_completed(self, repo):
        """A node in both is a contradiction the projector has to resolve; this
        column just needs a deterministic answer rather than dict order."""
        r, db = repo
        r.save_run("run-1", _ctx(node_visits={"a": 1},
                                 completed_nodes={"a"}, skipped_nodes={"a"}))
        assert _row(db, "a")["STATE"] == "skipped"


class TestOnlyTheTwoGateFieldsOpenARow:
    """Measured on tenant-1: ``node_visits`` is empty on **6,103 of 6,117** runs
    and ``pending_revisits`` on **6,115 of 6,117**. Gating on those two makes the
    whole write a no-op for 99.8% of runs. Letting ``completed_nodes`` open a row
    would invert that — it is non-empty on essentially every run — and put a
    delete-plus-N-inserts on every save, which is the write amplification this
    redesign exists to remove."""

    def test_a_node_with_nothing_is_not_written(self, repo):
        r, db = repo
        assert r.save_run("run-1", _ctx()) == 0
        assert db.rows == []

    def test_a_completed_node_alone_does_NOT_earn_a_row(self, repo):
        r, db = repo
        assert r.save_run("run-1", _ctx(
            completed_nodes={"c"}, skipped_nodes={"d"},
            last_output_ports={"e": "p"})) == 0
        assert db.rows == []

    def test_the_two_gate_fields_do(self, repo):
        r, db = repo
        written = r.save_run("run-1", _ctx(
            node_visits={"a": 1}, pending_revisits={"b"},
            completed_nodes={"c"}, skipped_nodes={"d"},
            last_output_ports={"e": "p"}))
        assert written == 2
        assert {row["NODE_ID"] for row in db.rows} == {"a", "b"}

    def test_a_zero_visit_count_does_not_earn_one_either(self, repo):
        """``node_visits`` carries ``{node: 0}`` for a node on its first visit —
        that is the absence of a cycle, not the presence of one."""
        r, db = repo
        assert r.save_run("run-1", _ctx(node_visits={"a": 0})) == 0

    def test_a_node_in_several_sources_gets_ONE_row(self, repo):
        r, db = repo
        assert r.save_run("run-1", _ctx(
            node_visits={"a": 3}, pending_revisits={"a"},
            completed_nodes={"a"}, last_output_ports={"a": "p"})) == 1

    def test_rows_are_written_in_a_stable_order(self, repo):
        """Two saves of the same state should produce the same rows, so a diff
        of the table between runs shows real differences."""
        r, db = repo
        r.save_run("run-1", _ctx(node_visits={"z": 1, "a": 1, "m": 1}))
        assert [row["NODE_ID"] for row in db.rows] == ["a", "m", "z"]


class TestTheRunIsTheUnitOfWrite:
    def test_a_save_replaces_the_runs_previous_rows(self, repo):
        r, db = repo
        r.save_run("run-1", _ctx(node_visits={"a": 1, "b": 1}))
        r.save_run("run-1", _ctx(node_visits={"a": 2}))
        assert [(row["NODE_ID"], row["VISITS"]) for row in db.rows] == [("a", 2)]

    def test_a_drained_revisit_disappears(self, repo):
        """``check_run_completion`` drains ``pending_revisits`` once the node
        quiesces. A per-node upsert would leave the row behind as a revisit that
        never fires — which is the bug, not the fix."""
        r, db = repo
        r.save_run("run-1", _ctx(pending_revisits={"a"}, completed_nodes={"a"}))
        r.save_run("run-1", _ctx(completed_nodes={"a"}))
        assert r.get_run("run-1")["pending_revisits"] == set()

    def test_other_runs_are_untouched(self, repo):
        r, db = repo
        r.save_run("run-1", _ctx(node_visits={"a": 1}))
        r.save_run("run-2", _ctx(node_visits={"a": 1}))
        r.save_run("run-1", _ctx(node_visits={"a": 9}))
        assert {row["RUN_ID"] for row in db.rows} == {"run-1", "run-2"}
        assert len(db.rows) == 2

    def test_delete_run_removes_only_that_run(self, repo):
        r, db = repo
        r.save_run("run-1", _ctx(node_visits={"a": 1}))
        r.save_run("run-2", _ctx(node_visits={"a": 1}))
        r.delete_run("run-1")
        assert [row["RUN_ID"] for row in db.rows] == ["run-2"]


class TestItCannotTakeASaveDown:
    @pytest.mark.parametrize("junk", [None, "", 0])
    def test_no_run_id_writes_nothing(self, repo, junk):
        r, db = repo
        assert r.save_run(junk, _ctx(node_visits={"a": 1})) == 0
        assert db.writes == []

    def test_no_context_writes_nothing(self, repo):
        r, db = repo
        assert r.save_run("run-1", None) == 0
        assert db.writes == []

    def test_a_context_missing_the_fields_is_fine(self, repo):
        """Anything with the right attribute names works — the repo must not
        require a full ``RunContext`` to be constructible in a test."""
        class Bare:
            pass
        r, db = repo
        assert r.save_run("run-1", Bare()) == 0

    def test_a_long_node_id_is_truncated(self, repo):
        """NVARCHAR(128). An overflow raises HANA 274 and would take the save
        down with it."""
        r, db = repo
        r.save_run("run-1", _ctx(node_visits={"n" * 500: 1}))
        assert len(db.rows[0]["NODE_ID"]) == 128

    def test_a_long_output_port_is_truncated(self, repo):
        r, db = repo
        r.save_run("run-1", _ctx(node_visits={"a": 1},
                                 last_output_ports={"a": "p" * 500}))
        assert len(_row(db, "a")["LAST_OUTPUT_PORT"]) == 64

    def test_an_unknown_run_reads_empty(self, repo):
        r, _ = repo
        assert r.get_run("nope") == {"node_visits": {}, "pending_revisits": set()}

    @pytest.mark.parametrize("junk", [None, "", 0])
    def test_no_run_id_reads_empty(self, repo, junk):
        r, _ = repo
        assert r.get_run(junk) == {"node_visits": {}, "pending_revisits": set()}
