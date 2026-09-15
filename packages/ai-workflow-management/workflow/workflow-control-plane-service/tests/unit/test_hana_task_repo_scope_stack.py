"""HANA task repo persists a task's loop scope_stack.

Root cause of the tenant-1 event-driven-loop stall: the HANA task repo dropped
``scope_stack`` (it was never a persisted column), so a loop-body task reloaded
at completion had ``get_innermost_scope() is None`` and the loop-advance hook
could not identify it → the loop never advanced past iteration 0. In-process
tests passed because the memory repo shares the task object (scope intact).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects.loop_scope import LoopScope
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer4_frameworks.persistence.hana.hana_task_repo import HanaTaskRepository


def _repo() -> HanaTaskRepository:
    repo = HanaTaskRepository(MagicMock())
    repo._execute_write = MagicMock()          # capture the UPSERT
    return repo


def test_scope_stack_from_json_roundtrip():
    scopes = [
        LoopScope(loop_node_id="outer", invocation_id="a", iter=1, total=3),
        LoopScope(loop_node_id="inner", invocation_id="b", iter=2, total=5),
    ]
    raw = json.dumps([s.to_dict() for s in scopes])
    back = HanaTaskRepository._scope_stack_from_json(raw)
    assert [s.loop_node_id for s in back] == ["outer", "inner"]
    assert back[-1].iter == 2 and back[-1].invocation_id == "b"


def test_scope_stack_from_json_empty_and_none():
    assert HanaTaskRepository._scope_stack_from_json(None) is None
    assert HanaTaskRepository._scope_stack_from_json("") is None
    assert HanaTaskRepository._scope_stack_from_json("[]") is None
    assert HanaTaskRepository._scope_stack_from_json("null") is None


def test_save_persists_scope_stack_column():
    repo = _repo()
    task = Task(
        run_id="r1", node_id="body", status=TaskStatus.COMPLETED,
        scope_stack=[LoopScope(loop_node_id="loop", invocation_id="inv", iter=0, total=4)],
    )
    repo.save(task)

    sql, values = repo._execute_write.call_args.args
    assert "SCOPE_STACK" in sql, "save must include the SCOPE_STACK column"
    # the serialized scope_stack round-trips to the original scope
    serialized = next(v for v in values if isinstance(v, str) and "loop_node_id" in v)
    back = HanaTaskRepository._scope_stack_from_json(serialized)
    assert back[0].loop_node_id == "loop" and back[0].iter == 0


def test_save_scope_stack_none_when_not_a_loop_body():
    repo = _repo()
    task = Task(run_id="r1", node_id="plain", status=TaskStatus.COMPLETED)  # no scope
    repo.save(task)
    _sql, values = repo._execute_write.call_args.args
    # no scope_stack JSON among the values (all serialized loop scopes absent)
    assert not any(isinstance(v, str) and "loop_node_id" in v for v in values)


def test_save_falls_back_when_scope_stack_column_absent():
    # Self-healing: a not-yet-migrated DB (no SCOPE_STACK column) must NOT fail
    # every task write — fall back to the pre-050 column set.
    repo = HanaTaskRepository(MagicMock())
    calls: list = []

    def _fake_write(sql, params):
        calls.append(sql)
        if "SCOPE_STACK" in sql:
            raise Exception("(260, 'invalid column name: SCOPE_STACK')")

    repo._execute_write = _fake_write
    task = Task(
        run_id="r1", node_id="body", status=TaskStatus.COMPLETED,
        scope_stack=[LoopScope(loop_node_id="loop", invocation_id="i", iter=0, total=2)],
    )
    repo.save(task)   # must not raise

    assert any("SCOPE_STACK" in s for s in calls), "first attempt includes the column"
    assert any("SCOPE_STACK" not in s for s in calls), "then falls back without it"
    assert repo._has_scope_stack_col is False
    # subsequent saves skip the column entirely (no repeated failing attempt)
    calls.clear()
    repo.save(task)
    assert calls and all("SCOPE_STACK" not in s for s in calls)


def test_save_uses_column_when_present_and_caches():
    repo = _repo()  # _execute_write is a MagicMock that never raises
    task = Task(
        run_id="r1", node_id="body", status=TaskStatus.COMPLETED,
        scope_stack=[LoopScope(loop_node_id="loop", invocation_id="i", iter=0, total=2)],
    )
    repo.save(task)
    assert repo._has_scope_stack_col is True
    assert "SCOPE_STACK" in repo._execute_write.call_args.args[0]
