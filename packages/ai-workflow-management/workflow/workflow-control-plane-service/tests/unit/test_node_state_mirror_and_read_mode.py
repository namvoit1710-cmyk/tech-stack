"""``node_visits`` / ``pending_revisits`` mirror + ``NODE_STATE_READ_MODE``.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §13.1. Table from
migrations 055 / 058. Same pair as ``loop_state`` got in #1833 / #1836, because
they are the same class of field: neither is event-sourced, so ``DROP CONTEXT``
destroys both.

What makes this one different is the risk. ``node_visits`` feeds
``TaskFactory.create_task``'s ``{run}-{node}-v{n}`` id (``task_factory.py:77``).
A wrong value re-mints ``v0``, so every event id derived from that task collides
with the first iteration's and is swallowed by the ``(run_id, event_id)``
dedupe — the run does not error, it silently skips work. Hence a separate switch
from ``LOOP_STATE_READ_MODE``.
"""
from __future__ import annotations

import json

import pytest

from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer4_frameworks.persistence.hana.hana_run_repo import HanaRunRepository


class FakeDb:
    def __init__(self, context: dict | None = None) -> None:
        self.context = context if context is not None else {}
        self.version: int | None = None

    def execute_query(self, sql: str, params: tuple = ()) -> list[dict]:
        if "SELECT VERSION" in sql.upper():
            return [{"version": self.version}] if self.version is not None else []
        return [{"id": "run-1", "workflow_id": "wf", "workflow_version": "1",
                 "status": "RUNNING", "context": json.dumps(self.context)}]

    def execute_write(self, sql: str, params: tuple = ()) -> int:
        if sql.strip().upper().startswith("INSERT"):
            self.version = 1
        return 1

    def execute_write_returning_rowcount(self, sql: str, params: tuple = ()) -> int:
        self.version = (self.version or 0) + 1
        return 1


class SpyNodeStateRepo:
    def __init__(self, stored: dict | None = None, explode: bool = False) -> None:
        self.stored = stored if stored is not None else {
            "node_visits": {}, "pending_revisits": set()}
        self.saved: list[tuple[str, dict, set]] = []
        self.reads: list[str] = []
        self._explode = explode

    def save_run(self, run_id: str, context) -> int:
        if self._explode:
            raise RuntimeError("HANA said no")
        self.saved.append((run_id, dict(context.node_visits),
                           set(context.pending_revisits)))
        return 1

    def get_run(self, run_id: str) -> dict:
        self.reads.append(run_id)
        if self._explode:
            raise RuntimeError("HANA said no")
        return self.stored


def _run(**ctx) -> WorkflowRun:
    return WorkflowRun(id="run-1", workflow_id="wf", workflow_version="1",
                       status=RunStatus.RUNNING, context=RunContext(**ctx))


def _ctx_json(**ctx) -> dict:
    return {"variables": {}, "completed_nodes": [], **ctx}


# ────────────────────────────── the mirror ───────────────────────────


class TestTheMirror:
    def test_it_writes_when_there_are_visits(self):
        db, spy = FakeDb(), SpyNodeStateRepo()
        HanaRunRepository(db, node_state_repo=spy).save(
            _run(node_visits={"a": 2}))
        assert spy.saved and spy.saved[0][1] == {"a": 2}

    def test_it_writes_when_there_is_a_pending_revisit(self):
        db, spy = FakeDb(), SpyNodeStateRepo()
        HanaRunRepository(db, node_state_repo=spy).save(
            _run(pending_revisits={"a"}))
        assert spy.saved and spy.saved[0][2] == {"a"}

    def test_it_writes_on_the_cas_update_too(self):
        """A cycle bumps its counter as it goes, so essentially all of the
        interesting writes take the UPDATE path."""
        db, spy = FakeDb(), SpyNodeStateRepo()
        repo, run = HanaRunRepository(db, node_state_repo=spy), _run(
            node_visits={"a": 1})
        repo.save(run)
        run.context.node_visits["a"] = 2
        repo.save(run)
        assert [s[1] for s in spy.saved] == [{"a": 1}, {"a": 2}]

    def test_an_ordinary_run_never_touches_the_table(self):
        """Measured on tenant-1: ``node_visits`` is empty on 6,103 of 6,117 runs
        and ``pending_revisits`` on 6,115. Gating on ``completed_nodes`` — which
        this table also has a column for — would invert that and put a
        delete-plus-N-inserts on every save."""
        db, spy = FakeDb(), SpyNodeStateRepo()
        HanaRunRepository(db, node_state_repo=spy).save(
            _run(completed_nodes={"a", "b"}, last_output_ports={"a": "success"}))
        assert spy.saved == []

    def test_no_repo_configured_is_a_no_op(self):
        HanaRunRepository(FakeDb()).save(_run(node_visits={"a": 1}))

    def test_a_failure_never_fails_the_run(self):
        db, spy = FakeDb(), SpyNodeStateRepo(explode=True)
        run = _run(node_visits={"a": 1})
        HanaRunRepository(db, node_state_repo=spy).save(run)
        assert run.version == 1

    def test_a_failure_is_logged(self, caplog):
        db, spy = FakeDb(), SpyNodeStateRepo(explode=True)
        with caplog.at_level("WARNING"):
            HanaRunRepository(db, node_state_repo=spy).save(
                _run(node_visits={"a": 1}))
        assert any("node_state mirror failed" in r.message for r in caplog.records)


# ──────────────────────────── the read mode ──────────────────────────


CTX = {"node_visits": {"a": 1}, "pending_revisits": ["b"]}
TABLE = {"node_visits": {"a": 9}, "pending_revisits": {"z"}}


def _repo(mode: str, ctx: dict, table: dict, **kw):
    db = FakeDb(_ctx_json(**ctx))
    spy = SpyNodeStateRepo(table, **kw)
    return HanaRunRepository(db, node_state_repo=spy,
                             node_state_read_mode=mode), spy


class TestContextModeIsTheDefault:
    def test_it_serves_context(self):
        repo, spy = _repo("context", CTX, TABLE)
        run = repo.get_by_id("run-1")
        assert run.context.node_visits == {"a": 1}
        assert run.context.pending_revisits == {"b"}

    def test_it_never_queries(self):
        repo, spy = _repo("context", CTX, TABLE)
        repo.get_by_id("run-1")
        assert spy.reads == []

    @pytest.mark.parametrize("typo", ["Table", "on", "true", "", None])
    def test_an_unrecognised_mode_is_treated_as_context(self, typo):
        repo, spy = _repo(typo, CTX, TABLE)
        assert repo.get_by_id("run-1").context.node_visits == {"a": 1}
        assert spy.reads == []


class TestShadowMode:
    def test_it_serves_context_but_reads_both(self):
        repo, spy = _repo("shadow", CTX, TABLE)
        assert repo.get_by_id("run-1").context.node_visits == {"a": 1}
        assert spy.reads == ["run-1"]

    def test_a_divergence_is_logged(self, caplog):
        repo, spy = _repo("shadow", CTX, TABLE)
        with caplog.at_level("WARNING"):
            repo.get_by_id("run-1")
        assert any("node_state divergence" in r.message for r in caplog.records)

    def test_agreement_is_silent(self, caplog):
        repo, spy = _repo("shadow", CTX,
                          {"node_visits": {"a": 1}, "pending_revisits": {"b"}})
        with caplog.at_level("WARNING"):
            repo.get_by_id("run-1")
        assert not any("divergence" in r.message for r in caplog.records)

    def test_an_ordinary_run_never_queries(self):
        repo, spy = _repo("shadow", {"node_visits": {}, "pending_revisits": []},
                          TABLE)
        repo.get_by_id("run-1")
        assert spy.reads == []


class TestTableMode:
    def test_it_serves_the_table(self):
        repo, spy = _repo("table", CTX, TABLE)
        run = repo.get_by_id("run-1")
        assert run.context.node_visits == {"a": 9}
        assert run.context.pending_revisits == {"z"}

    def test_it_falls_back_per_FIELD_not_per_row(self):
        """A run can legitimately have visits and no revisits. Treating the
        empty half as "the table has nothing" would keep serving a stale
        CONTEXT for it forever."""
        repo, spy = _repo("table", CTX,
                          {"node_visits": {"a": 9}, "pending_revisits": set()})
        run = repo.get_by_id("run-1")
        assert run.context.node_visits == {"a": 9}      # from the table
        assert run.context.pending_revisits == {"b"}    # fell back

    def test_an_empty_table_falls_back_entirely(self):
        """Runs written before the mirror existed have no rows. Serving empty
        for those would re-mint v0 and let the dedupe swallow the next
        iteration."""
        repo, spy = _repo("table", CTX,
                          {"node_visits": {}, "pending_revisits": set()})
        run = repo.get_by_id("run-1")
        assert run.context.node_visits == {"a": 1}

    def test_it_queries_even_when_context_is_empty(self):
        repo, spy = _repo("table", {"node_visits": {}, "pending_revisits": []},
                          TABLE)
        assert repo.get_by_id("run-1").context.node_visits == {"a": 9}
        assert spy.reads == ["run-1"]


class TestAFailedReadNeverBreaksTheLoad:
    def test_it_serves_context(self):
        repo, spy = _repo("table", CTX, TABLE, explode=True)
        assert repo.get_by_id("run-1").context.node_visits == {"a": 1}

    def test_it_is_logged(self, caplog):
        repo, spy = _repo("table", CTX, TABLE, explode=True)
        with caplog.at_level("WARNING"):
            repo.get_by_id("run-1")
        assert any("node_state read failed" in r.message for r in caplog.records)


class TestListPathsDoNotPayForIt:
    def test_list_by_workflow_never_queries(self):
        repo, spy = _repo("table", CTX, TABLE)
        repo.list_by_workflow("wf")
        assert spy.reads == []

    def test_get_by_root_id_never_queries(self):
        repo, spy = _repo("table", CTX, TABLE)
        repo.get_by_root_id("root-1")
        assert spy.reads == []
