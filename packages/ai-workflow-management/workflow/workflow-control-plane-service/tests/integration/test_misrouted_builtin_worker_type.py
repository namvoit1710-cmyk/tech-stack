"""Fail-fast guard for a node mis-typed onto the worker-dispatch path.

Reproduces the tenant-1 hang where a sub-workflow node was authored as
``node_type=TASK`` + ``worker_type="builtin:workflow"`` (instead of the correct
``node_type=WORKFLOW``). Because the engine classifies by ``node_type``, such a
node reaches ``_run_dispatched`` and is "dispatched" to a worker named
``builtin:workflow`` — which never exists (the dispatchers drop ``builtin:*``),
so the task sat RUNNING forever with no error and the run stalled.

The guard fails the task immediately with an actionable message. It is keyed off
the node-spec ``runtime`` (single source of truth), NOT the ``builtin:`` prefix,
so a builtin that later migrates to executor/worker execution is NOT affected.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)
from app.layer2_application.orchestration.node_execution_runner import (
    _engine_inline_builtin_type,
)
from bootstrap import build_app_container
from tests.helpers.queue_runner import QueueRunner


@pytest.fixture
def container():
    return build_app_container()


async def _drain(container) -> None:
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()


# ── unit: the pure classifier ────────────────────────────────────────

def test_engine_inline_builtin_type_flags_engine_types():
    # WORKFLOW / OUTPUT are runtime="engine" builtins → flagged.
    assert _engine_inline_builtin_type("builtin:workflow") == NodeType.WORKFLOW
    assert _engine_inline_builtin_type("builtin:output") == NodeType.OUTPUT


def test_engine_inline_builtin_type_ignores_non_engine():
    # A real external worker type — never flagged.
    assert _engine_inline_builtin_type("worker:http_request") is None
    # PENDING builtins run inline-but-paused, not engine → not this guard's job.
    assert _engine_inline_builtin_type("builtin:input") is None
    # TASK's own minimal spec is runtime="worker" → not flagged.
    assert _engine_inline_builtin_type("builtin:task") is None
    # Unknown / malformed / empty.
    assert _engine_inline_builtin_type("builtin:not_a_type") is None
    assert _engine_inline_builtin_type("") is None
    assert _engine_inline_builtin_type(None) is None


# ── integration: the mis-typed node fails fast instead of hanging ────

def _misrouted_workflow() -> Workflow:
    """TRIGGER → (mis-typed) TASK carrying worker_type=builtin:workflow.

    Mirrors tenant-1 workflow 9eaa3013's ``sub_tpl`` node shape.
    """
    return Workflow(
        id="misrouted-wf", name="misrouted",
        nodes=[
            WorkflowNode(id="trig", name="t", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="sub_tpl", name="Template", node_type=NodeType.TASK,
                worker_type="builtin:workflow",
                config={"workflow_id": "some-child", "input_mapping": {}},
            ),
        ],
        edges=[WorkflowEdge(id="e1", source_node_id="trig", target_node_id="sub_tpl")],
    )


def _real_worker_workflow() -> Workflow:
    """TRIGGER → a legit external-worker TASK (control: must NOT be failed)."""
    return Workflow(
        id="real-worker-wf", name="real-worker",
        nodes=[
            WorkflowNode(id="trig", name="t", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="call", name="Call", node_type=NodeType.TASK,
                worker_type="worker:http_request",
                config={},
            ),
        ],
        edges=[WorkflowEdge(id="e1", source_node_id="trig", target_node_id="call")],
    )


@pytest.mark.asyncio
async def test_misrouted_builtin_workflow_node_fails_fast(container):
    container["_infra"].repos.workflow.save(_misrouted_workflow())

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="misrouted-wf", input_data={})
    )
    assert not result.error, f"start_run failed: {result.error}"
    run_id = result.id
    await _drain(container)

    run_repo = container["_infra"].repos.run
    task_repo = container["_infra"].repos.task

    # The run must NOT be left hanging — it fails fast.
    run = run_repo.get_by_id(run_id)
    assert run.status == RunStatus.FAILED, (
        f"expected FAILED, got {run.status} — mis-typed node hung again"
    )

    # The failing task carries the actionable diagnosis.
    tasks = [t for t in task_repo.list_by_run(run_id) if t.node_id == "sub_tpl"]
    assert tasks, "sub_tpl task was never created"
    task = tasks[0]
    assert task.status == TaskStatus.FAILED
    assert (task.output_metadata or {}).get("error_code") == "MisroutedInlineNode"
    assert "mis-typed" in (task.error or "")
    assert "node_type=WORKFLOW" in (task.error or "")


@pytest.mark.asyncio
async def test_real_worker_task_is_not_failed_by_guard(container):
    """Guard is targeted: a legit external-worker TASK still dispatches and
    awaits its worker (stays non-terminal), it is NOT fail-fasted."""
    container["_infra"].repos.workflow.save(_real_worker_workflow())

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="real-worker-wf", input_data={})
    )
    assert not result.error, f"start_run failed: {result.error}"
    run_id = result.id
    await _drain(container)

    task_repo = container["_infra"].repos.task
    tasks = [t for t in task_repo.list_by_run(run_id) if t.node_id == "call"]
    assert tasks, "worker TASK was never created"
    # Dispatched and awaiting the worker — NOT failed by the guard.
    assert tasks[0].status not in {TaskStatus.FAILED, TaskStatus.COMPLETED}
