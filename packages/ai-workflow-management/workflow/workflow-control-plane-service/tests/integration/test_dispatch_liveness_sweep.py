"""Liveness sweep (Signal A): fail a dispatched TASK whose worker_type has no
live worker, with safety layers that never false-fail a long-running task whose
worker type IS alive.

Covers: fail-when-type-dead, liveness-exempt (type alive), grace (too young),
empty-registry skip, and disabled-by-default no-op.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)
from app.layer4_frameworks.config.app_config import settings
from bootstrap import build_app_container
from tests.helpers.queue_runner import QueueRunner


@pytest.fixture
def container():
    return build_app_container()


async def _drain(container) -> None:
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()


class _FakeRegistry:
    def __init__(self, worker_types):
        self._types = worker_types

    def list_workers(self):
        return [{"worker_type": t} for t in self._types]


def _worker_workflow() -> Workflow:
    """TRIGGER → external-worker TASK (worker:http_request)."""
    return Workflow(
        id="wf-worker", name="w",
        nodes=[
            WorkflowNode(id="trig", name="t", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="call", name="Call", node_type=NodeType.TASK,
                worker_type="worker:http_request", config={},
            ),
        ],
        edges=[WorkflowEdge(id="e1", source_node_id="trig", target_node_id="call")],
    )


async def _start_stuck_task(container):
    """Start the workflow and drain — the worker TASK ends up RUNNING (dispatched,
    awaiting a worker that never answers in tests). Returns (run_id, task)."""
    container["_infra"].repos.workflow.save(_worker_workflow())
    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="wf-worker", input_data={})
    )
    assert not result.error, f"start_run failed: {result.error}"
    await _drain(container)
    task_repo = container["_infra"].repos.task
    task = next(t for t in task_repo.list_by_run(result.id) if t.node_id == "call")
    assert task.status == TaskStatus.RUNNING, "precondition: worker task should be dispatched/RUNNING"
    return result.id, task


@pytest.mark.asyncio
async def test_fails_task_when_worker_type_has_no_live_worker(container, monkeypatch):
    run_id, _ = await _start_stuck_task(container)
    uc = container["sweep_unservable_dispatched_tasks"]
    # Registry is UP (reports some workers) but NOT the type we need.
    uc._registry = _FakeRegistry(["worker:other"])
    monkeypatch.setattr(settings, "WORKER_DISPATCH_LIVENESS_SWEEP_ENABLED", True)
    monkeypatch.setattr(settings, "WORKER_DISPATCH_LIVENESS_GRACE_SECONDS", 0)

    out = await uc.execute()
    assert out["swept"] == 1, out

    task_repo = container["_infra"].repos.task
    task = next(t for t in task_repo.list_by_run(run_id) if t.node_id == "call")
    assert task.status == TaskStatus.FAILED
    assert "No live worker" in (task.error or "")
    assert "worker:http_request" in (task.error or "")


@pytest.mark.asyncio
async def test_does_not_fail_when_type_is_live(container, monkeypatch):
    """Liveness exemption — a live worker of the type keeps the task safe even
    with grace=0 (this is what protects long-running tasks)."""
    run_id, _ = await _start_stuck_task(container)
    uc = container["sweep_unservable_dispatched_tasks"]
    uc._registry = _FakeRegistry(["worker:http_request"])  # the type IS alive
    monkeypatch.setattr(settings, "WORKER_DISPATCH_LIVENESS_SWEEP_ENABLED", True)
    monkeypatch.setattr(settings, "WORKER_DISPATCH_LIVENESS_GRACE_SECONDS", 0)

    out = await uc.execute()
    assert out["swept"] == 0, out
    task_repo = container["_infra"].repos.task
    task = next(t for t in task_repo.list_by_run(run_id) if t.node_id == "call")
    assert task.status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_grace_protects_young_tasks(container, monkeypatch):
    run_id, _ = await _start_stuck_task(container)
    uc = container["sweep_unservable_dispatched_tasks"]
    uc._registry = _FakeRegistry(["worker:other"])  # type dead
    monkeypatch.setattr(settings, "WORKER_DISPATCH_LIVENESS_SWEEP_ENABLED", True)
    monkeypatch.setattr(settings, "WORKER_DISPATCH_LIVENESS_GRACE_SECONDS", 99999)

    out = await uc.execute()
    assert out["swept"] == 0, out
    task_repo = container["_infra"].repos.task
    task = next(t for t in task_repo.list_by_run(run_id) if t.node_id == "call")
    assert task.status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_empty_registry_skips_tick(container, monkeypatch):
    """A registry snapshot with zero live workers (blip / mass restart) must NOT
    fail every dispatched task — the whole tick is skipped."""
    run_id, _ = await _start_stuck_task(container)
    uc = container["sweep_unservable_dispatched_tasks"]
    uc._registry = _FakeRegistry([])  # empty snapshot
    monkeypatch.setattr(settings, "WORKER_DISPATCH_LIVENESS_SWEEP_ENABLED", True)
    monkeypatch.setattr(settings, "WORKER_DISPATCH_LIVENESS_GRACE_SECONDS", 0)

    out = await uc.execute()
    assert out["swept"] == 0
    assert out.get("reason") == "empty_registry", out
    task_repo = container["_infra"].repos.task
    task = next(t for t in task_repo.list_by_run(run_id) if t.node_id == "call")
    assert task.status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_disabled_by_default_is_noop(container, monkeypatch):
    run_id, _ = await _start_stuck_task(container)
    uc = container["sweep_unservable_dispatched_tasks"]
    uc._registry = _FakeRegistry(["worker:other"])
    monkeypatch.setattr(settings, "WORKER_DISPATCH_LIVENESS_SWEEP_ENABLED", False)

    out = await uc.execute()
    assert out == {"swept": 0, "enabled": False}
    task_repo = container["_infra"].repos.task
    task = next(t for t in task_repo.list_by_run(run_id) if t.node_id == "call")
    assert task.status == TaskStatus.RUNNING
