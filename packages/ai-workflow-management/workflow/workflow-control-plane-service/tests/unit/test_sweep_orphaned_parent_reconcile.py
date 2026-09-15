"""Regression: a parent waiting on a child that has ALREADY finished must be
reconciled — nothing else will ever move it.

Two independent blind spots put this state outside every existing mechanism:

  * ``_find_timed_out_tracking_tasks`` enumerates ``statuses=[RUNNING]`` child
    runs. A child that already reached FAILED is not in that list at all.
  * even if it were, ``_check_child_timeout`` returns ``None`` unless the
    parent's WORKFLOW node carries an explicit positive ``timeout_ms``, and
    every ``builtin:workflow`` node built in the UI leaves it unset.

Observed on tenant-1 2026-08-25: a parent sat RUNNING for 20 minutes at its
WORKFLOW node while the child run had been FAILED for 19 of them, until a human
cancelled it. The cause of that particular child failure is fixed separately
(see ``tests/integration/test_child_failure_notifies_parent.py``); this is the
backstop for every other way the notification can go missing, a control-plane
restart between the two writes included.

Default mode is ``shadow`` — it finds and logs, and changes nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.sweep_timed_out_child_runs.use_cases.sweep_timed_out_child_runs_use_case import (  # noqa: E501
    SweepTimedOutChildRunsUseCase,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import (
    MemoryRunRepository,
)
from app.layer4_frameworks.persistence.memory.memory_task_repo import (
    MemoryTaskRepository,
)

WF_NODE = "wf_node"


def _iso_ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[dict] = []

    def info(self, *a, **k) -> None: ...
    def error(self, *a, **k) -> None: ...

    def warning(self, msg="", *args, **k) -> None:
        # Mirrors StandardLogger: %-style args interpolate into the message,
        # kwargs ride along as `extra=`. The rendered message is what an
        # operator actually sees, so the test asserts on it.
        rendered = (msg % args) if args else msg
        self.warnings.append({"msg": rendered, **k})


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
    def __init__(self, workflow: _Workflow) -> None:
        self._workflow = workflow
        self.failed_parents: list[str] = []
        self.completed_parents: list[str] = []

    def load_workflow_for_run(self, run):
        return self._workflow

    def emit_run_status(self, run, status, **k) -> None: ...
    def emit_node_cancelled(self, run, **k) -> None: ...

    async def fail_parent_tracking_task(self, child_run) -> None:
        self.failed_parents.append(child_run.id)

    async def _complete_parent_tracking_task(self, child_run) -> None:
        self.completed_parents.append(child_run.id)


class _Config:
    """Only the fields the reconciliation reads."""

    def __init__(self, mode: str = "on", grace: int = 60, limit: int = 200) -> None:
        self.CHILD_ORPHAN_RECONCILE_MODE = mode
        self.CHILD_ORPHAN_GRACE_SECONDS = grace
        self.CHILD_ORPHAN_SCAN_LIMIT = limit
        self.CHILD_ORPHAN_SCAN_INTERVAL_SECONDS = 60
        # No WORKFLOW node here carries a timeout, so the timeout half of the
        # sweep is a no-op throughout this file — as it is in production.
        self.CHILD_TIMEOUT_SWEEP_INTERVAL_SECONDS = 5


def _wire(
    *,
    mode: str = "on",
    child_status: RunStatus = RunStatus.FAILED,
    child_finished_ago: float = 600.0,
    tracking_status: TaskStatus = TaskStatus.RUNNING,
    grace: int = 60,
):
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()

    run_repo.save(WorkflowRun(
        id="parent", status=RunStatus.RUNNING, depth_level=0,
        started_at=_iso_ago(child_finished_ago + 60),
        context=RunContext(child_runs={WF_NODE: "child"}),
    ))
    run_repo.save(WorkflowRun(
        id="child", status=child_status, depth_level=1,
        parent_run_id="parent", parent_node_id=WF_NODE,
        started_at=_iso_ago(child_finished_ago + 30),
        completed_at=(
            None if child_status is RunStatus.RUNNING
            else _iso_ago(child_finished_ago)
        ),
        error="Run 'child': expected version 7, found 8",
        context=RunContext(),
    ))
    task_repo.save(Task(
        id="tt", run_id="parent", node_id=WF_NODE, status=tracking_status,
        created_at=_iso_ago(child_finished_ago + 30),
    ))

    logger = _Logger()
    # No ``timeout_ms`` — the production default, and the reason the existing
    # timeout sweep cannot see this run.
    engine = _Engine(_Workflow(_Node(WF_NODE, {})))
    uc = SweepTimedOutChildRunsUseCase(
        logger=logger, task_repo=task_repo, run_repo=run_repo,
        orchestration_engine=engine, app_config=_Config(mode, grace),
    )
    return uc, engine, logger, run_repo, task_repo


@pytest.mark.asyncio
async def test_failed_child_with_a_live_tracking_task_is_reconciled() -> None:
    uc, engine, _logger, *_ = _wire(mode="on")
    result = await uc.execute()
    assert result["orphans"] == 1
    assert engine.failed_parents == ["child"], (
        "the parent's tracking task was never failed — this is the state that "
        "hung a parent for 20 minutes on tenant-1"
    )


@pytest.mark.asyncio
async def test_completed_child_completes_the_parent_task() -> None:
    uc, engine, *_ = _wire(mode="on", child_status=RunStatus.COMPLETED)
    result = await uc.execute()
    assert result["orphans"] == 1
    assert engine.completed_parents == ["child"]
    assert engine.failed_parents == []


@pytest.mark.asyncio
async def test_shadow_mode_reports_but_changes_nothing() -> None:
    uc, engine, logger, *_ = _wire(mode="shadow")
    result = await uc.execute()
    assert result["orphans"] == 1
    assert engine.failed_parents == [] and engine.completed_parents == []
    orphan_logs = [w for w in logger.warnings if w.get("child_run_id") == "child"]
    assert orphan_logs, "shadow mode must still say what it found"
    assert orphan_logs[0]["mode"] == "shadow"
    # The rendered MESSAGE has to carry the facts. StandardLogger hands kwargs
    # to `extra=` and the deployed formatter renders none of it — on tenant-1
    # the shadow line arrived as a bare "Orphaned parent tracking task", a
    # count with nothing to act on.
    rendered = orphan_logs[0]["msg"]
    for fact in ("shadow", "parent", "child", WF_NODE):
        assert fact in rendered, f"{fact!r} missing from the log line: {rendered!r}"


@pytest.mark.asyncio
async def test_off_mode_does_not_even_scan() -> None:
    uc, engine, logger, *_ = _wire(mode="off")
    result = await uc.execute()
    assert result["orphans"] == 0
    assert not logger.warnings
    assert engine.failed_parents == []


@pytest.mark.asyncio
async def test_inside_the_grace_window_is_left_alone() -> None:
    """The normal notification path gets first refusal. A child that finished
    two seconds ago is not an orphan — it is in flight."""
    uc, engine, *_ = _wire(mode="on", child_finished_ago=2.0, grace=60)
    result = await uc.execute()
    assert result["orphans"] == 0
    assert engine.failed_parents == []


@pytest.mark.asyncio
async def test_terminal_tracking_task_is_not_an_orphan() -> None:
    uc, engine, *_ = _wire(mode="on", tracking_status=TaskStatus.COMPLETED)
    result = await uc.execute()
    assert result["orphans"] == 0
    assert engine.failed_parents == []


@pytest.mark.asyncio
async def test_running_child_is_left_to_the_timeout_sweep() -> None:
    uc, engine, *_ = _wire(mode="on", child_status=RunStatus.RUNNING)
    result = await uc.execute()
    assert result["orphans"] == 0
    assert engine.failed_parents == []


@pytest.mark.asyncio
async def test_the_scan_runs_on_its_own_cadence_not_the_5s_tick() -> None:
    """The reconciliation rides the timeout sweep's 5s tick but must not pay
    its cadence: each pass costs a RUNNING-runs query plus a run read and a
    task list per parent, and there is nothing a 5s scan finds that a 60s scan
    does not (the grace window is 60s anyway)."""
    uc, engine, *_ = _wire(mode="on")
    first = await uc.execute()
    assert first["orphans"] == 1

    # Second tick, five seconds later in the real service: skipped.
    second = await uc.execute()
    assert second["orphans"] == 0
    assert engine.failed_parents == ["child"], (
        "the second tick re-reconciled — the throttle is not holding"
    )

    # Wind the clock past the interval and it scans again.
    uc._last_orphan_scan = datetime.now(timezone.utc) - timedelta(seconds=3600)
    third = await uc.execute()
    assert third["orphans"] == 1
