"""Regression: the child-run timeout sweeper finds + fails timed-out child
workflow runs (bug #4 — dead sweeper).

Before the fix ``execute()`` called ``task_repo.get_timed_out_tracking_tasks()``,
a method defined nowhere, so every sweep cycle raised ``AttributeError``
(swallowed by ``main.py``'s loop) and no child run ever timed out. The sweep
now discovers timed-out children itself: it enumerates active child runs
(``depth_level > 0``), reads the parent WORKFLOW node's ``timeout_ms``, and
fails the child once that bound has elapsed. Only nodes with an explicit
positive ``timeout_ms`` are swept.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer4_frameworks.persistence.memory.memory_run_repo import (
    MemoryRunRepository,
)
from app.layer4_frameworks.persistence.memory.memory_task_repo import (
    MemoryTaskRepository,
)
from app.layer2_application.features.sweep_timed_out_child_runs.use_cases.sweep_timed_out_child_runs_use_case import (  # noqa: E501
    SweepTimedOutChildRunsUseCase,
)

WF_NODE = "wf_node"


def _iso_ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


class _Logger:
    def info(self, *a, **k) -> None: ...
    def error(self, *a, **k) -> None: ...
    def warning(self, *a, **k) -> None: ...


class _Node:
    def __init__(self, node_id: str, config: dict) -> None:
        self.id = node_id
        self.config = config


class _Workflow:
    name = "child-wf"

    def __init__(self, *nodes: _Node) -> None:
        self._nodes = {n.id: n for n in nodes}

    def get_node(self, node_id: str):
        return self._nodes.get(node_id)


class _Engine:
    """Minimal engine double covering the surface the sweeper touches."""

    def __init__(self, workflow: _Workflow) -> None:
        self._workflow = workflow
        self.failed_parents: list[str] = []
        self.run_statuses: list[tuple[str, str]] = []

    def load_workflow_for_run(self, run):
        return self._workflow

    def emit_run_status(self, run, status, **k) -> None:
        self.run_statuses.append((run.id, status))

    def emit_node_cancelled(self, run, **k) -> None: ...

    async def fail_parent_tracking_task(self, child_run) -> None:
        self.failed_parents.append(child_run.id)


def _wire(
    *,
    timeout_ms,
    tracking_status: TaskStatus = TaskStatus.RUNNING,
    child_started_ago: float = 5.0,
    child_status: RunStatus = RunStatus.RUNNING,
):
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()

    parent = WorkflowRun(
        id="parent", status=RunStatus.RUNNING, depth_level=0,
        context=RunContext(child_runs={WF_NODE: "child"}),
    )
    child = WorkflowRun(
        id="child", status=child_status, depth_level=1,
        parent_run_id="parent", parent_node_id=WF_NODE,
        started_at=_iso_ago(child_started_ago), context=RunContext(),
    )
    run_repo.save(parent)
    run_repo.save(child)

    task_repo.save(Task(
        id="tt", run_id="parent", node_id=WF_NODE, status=tracking_status,
        created_at=_iso_ago(child_started_ago),
    ))

    config = {"timeout_ms": timeout_ms} if timeout_ms is not None else {}
    engine = _Engine(_Workflow(_Node(WF_NODE, config)))
    uc = SweepTimedOutChildRunsUseCase(
        logger=_Logger(), task_repo=task_repo, run_repo=run_repo,
        orchestration_engine=engine,
    )
    return uc, engine, run_repo, task_repo


@pytest.mark.asyncio
async def test_finds_timed_out_child() -> None:
    uc, _engine, *_ = _wire(timeout_ms=1000, child_started_ago=5.0)
    found = await uc._find_timed_out_tracking_tasks()
    assert len(found) == 1
    task, timeout_ms = found[0]
    assert task.id == "tt"
    assert timeout_ms == 1000


@pytest.mark.asyncio
async def test_no_timeout_configured_is_never_swept() -> None:
    """A WORKFLOW node without an explicit ``timeout_ms`` is left alone — this
    is what protects a child legitimately paused on a HUMAN_ACTION node."""
    uc, *_ = _wire(timeout_ms=None, child_started_ago=5.0)
    assert await uc._find_timed_out_tracking_tasks() == []


@pytest.mark.asyncio
async def test_within_timeout_window_is_not_swept() -> None:
    uc, *_ = _wire(timeout_ms=60_000, child_started_ago=1.0)
    assert await uc._find_timed_out_tracking_tasks() == []


@pytest.mark.asyncio
async def test_terminal_tracking_task_is_not_swept() -> None:
    uc, *_ = _wire(timeout_ms=1000, tracking_status=TaskStatus.COMPLETED)
    assert await uc._find_timed_out_tracking_tasks() == []


@pytest.mark.asyncio
async def test_execute_fails_the_timed_out_child() -> None:
    uc, engine, run_repo, _ = _wire(timeout_ms=1000, child_started_ago=5.0)
    result = await uc.execute()
    assert result["swept"] == 1
    assert engine.failed_parents == ["child"]
    assert ("child", "failed") in engine.run_statuses
    child = await run_repo.aget_by_id("child")
    assert "timed out after 1000ms" in (child.error or "")


@pytest.mark.asyncio
async def test_one_malformed_run_does_not_sink_the_sweep() -> None:
    """A throw while checking one child must not abort the whole sweep — else
    ``main.py``'s ``except`` swallows it and we're back to the silent no-op."""
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()

    good_parent = WorkflowRun(
        id="gp", status=RunStatus.RUNNING, depth_level=0,
        context=RunContext(child_runs={WF_NODE: "gc"}),
    )
    good_child = WorkflowRun(
        id="gc", status=RunStatus.RUNNING, depth_level=1,
        parent_run_id="gp", parent_node_id=WF_NODE,
        started_at=_iso_ago(5.0), context=RunContext(),
    )
    bad_parent = WorkflowRun(
        id="bp", status=RunStatus.RUNNING, depth_level=0,
        context=RunContext(child_runs={WF_NODE: "bc"}),
    )
    bad_child = WorkflowRun(
        id="bc", status=RunStatus.RUNNING, depth_level=1,
        parent_run_id="bp", parent_node_id=WF_NODE,
        started_at=_iso_ago(5.0), context=RunContext(),
    )
    for r in (good_parent, good_child, bad_parent, bad_child):
        run_repo.save(r)
    task_repo.save(Task(id="gtt", run_id="gp", node_id=WF_NODE,
                        status=TaskStatus.RUNNING))

    class _RaisingEngine(_Engine):
        def load_workflow_for_run(self, run):
            if run.id == "bp":
                raise RuntimeError("boom loading bad parent workflow")
            return self._workflow

    engine = _RaisingEngine(_Workflow(_Node(WF_NODE, {"timeout_ms": 1000})))
    uc = SweepTimedOutChildRunsUseCase(
        logger=_Logger(), task_repo=task_repo, run_repo=run_repo,
        orchestration_engine=engine,
    )

    found = await uc._find_timed_out_tracking_tasks()
    assert [t.id for t, _ in found] == ["gtt"]
