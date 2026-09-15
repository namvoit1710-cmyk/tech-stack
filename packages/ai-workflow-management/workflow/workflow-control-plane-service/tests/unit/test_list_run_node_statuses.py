"""Tests for ListRunNodeStatusesUseCase."""

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects import NodeType, RunStatus, TaskStatus
from app.layer1_domain.value_objects.loop_scope import LoopScope
from app.layer2_application.features.list_run_node_statuses.use_cases.list_run_node_statuses_use_case import (
    ListRunNodeStatusesCommand,
    ListRunNodeStatusesUseCase,
)
from tests.conftest import DummyLogger


@pytest.fixture
def usecase(workflow_repo, run_repo, task_repo):
    return ListRunNodeStatusesUseCase(
        logger=DummyLogger(),
        run_repo=run_repo,
        task_repo=task_repo,
        workflow_repo=workflow_repo,
    )


@pytest.fixture
def workflow_with_subflow():
    return Workflow(
        id="wf-1",
        name="With Subflow",
        nodes=[
            WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(id="task-a", name="A", node_type=NodeType.TASK, worker_type="w1"),
            WorkflowNode(id="subflow", name="Sub", node_type=NodeType.WORKFLOW),
            WorkflowNode(id="task-b", name="B", node_type=NodeType.TASK, worker_type="w2"),
        ],
        edges=[
            WorkflowEdge(source_node_id="trigger", target_node_id="task-a"),
            WorkflowEdge(source_node_id="task-a", target_node_id="subflow"),
            WorkflowEdge(source_node_id="subflow", target_node_id="task-b"),
        ],
    )


class TestListRunNodeStatuses:
    async def test_run_not_found(self, usecase):
        result = await usecase.execute(ListRunNodeStatusesCommand(run_id="missing"))
        assert result.error == "Run not found"
        assert result.error_code == "RUN_NOT_FOUND"

    async def test_workflow_not_found(self, usecase, run_repo):
        run = WorkflowRun(id="r1", workflow_id="ghost", status=RunStatus.RUNNING)
        run_repo.save(run)
        result = await usecase.execute(ListRunNodeStatusesCommand(run_id="r1"))
        assert result.error == "Workflow not found"
        assert result.error_code == "WORKFLOW_NOT_FOUND"

    async def test_run_with_no_tasks_yet_marks_nodes_not_started(
        self, usecase, workflow_repo, run_repo, workflow_with_subflow,
    ):
        workflow_repo.save(workflow_with_subflow)
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.PENDING))
        result = await usecase.execute(ListRunNodeStatusesCommand(run_id="r1"))
        assert result.error is None
        assert result.run_id == "r1"
        assert result.workflow_id == "wf-1"
        assert result.status == "PENDING"
        assert len(result.nodes) == 4
        for node in result.nodes:
            assert node["status"] == "NOT_STARTED"
            assert node["child_run_id"] is None
            assert node["error"] is None

    async def test_terminal_run_marks_unreached_nodes_skipped(
        self, usecase, workflow_repo, run_repo, task_repo, workflow_with_subflow,
    ):
        workflow_repo.save(workflow_with_subflow)
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.FAILED))
        task_repo.save(Task(
            id="t1", run_id="r1", node_id="task-a", status=TaskStatus.FAILED,
            error="boom",
            started_at="2025-01-01T00:00:00Z", completed_at="2025-01-01T00:00:01Z",
        ))
        result = await usecase.execute(ListRunNodeStatusesCommand(run_id="r1"))
        by_id = {n["node_id"]: n for n in result.nodes}
        # The node that actually failed keeps FAILED.
        assert by_id["task-a"]["status"] == "FAILED"
        # Nodes the run never reached are SKIPPED (not FAILED — they didn't fail).
        assert by_id["task-b"]["status"] == "SKIPPED"
        assert by_id["subflow"]["status"] == "SKIPPED"
        assert by_id["trigger"]["status"] == "SKIPPED"

    async def test_mixed_statuses_aggregate_per_node(
        self, usecase, workflow_repo, run_repo, task_repo, workflow_with_subflow,
    ):
        workflow_repo.save(workflow_with_subflow)
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING))
        task_repo.save(Task(
            id="t1", run_id="r1", node_id="task-a", status=TaskStatus.COMPLETED,
            started_at="2025-01-01T00:00:00Z", completed_at="2025-01-01T00:00:30Z",
        ))
        task_repo.save(Task(
            id="t2a", run_id="r1", node_id="task-b", status=TaskStatus.COMPLETED,
            started_at="2025-01-01T00:01:00Z", completed_at="2025-01-01T00:01:10Z",
        ))
        task_repo.save(Task(
            id="t2b", run_id="r1", node_id="task-b", status=TaskStatus.RUNNING,
            started_at="2025-01-01T00:01:05Z",
        ))

        result = await usecase.execute(ListRunNodeStatusesCommand(run_id="r1"))

        by_id = {n["node_id"]: n for n in result.nodes}
        assert by_id["task-a"]["status"] == "COMPLETED"
        assert by_id["task-a"]["started_at"] == "2025-01-01T00:00:00Z"
        assert by_id["task-a"]["completed_at"] == "2025-01-01T00:00:30Z"
        assert by_id["task-b"]["status"] == "RUNNING"
        assert by_id["task-b"]["started_at"] == "2025-01-01T00:01:00Z"
        # task-b is RUNNING (not terminal) so completed_at not surfaced
        assert by_id["task-b"]["completed_at"] is None
        assert by_id["trigger"]["status"] == "NOT_STARTED"  # no tasks yet, run still in flight

    async def test_failed_node_includes_last_error(
        self, usecase, workflow_repo, run_repo, task_repo, workflow_with_subflow,
    ):
        workflow_repo.save(workflow_with_subflow)
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.FAILED))
        task_repo.save(Task(
            id="t1", run_id="r1", node_id="task-a", status=TaskStatus.FAILED,
            error="Worker timeout after 30s",
            started_at="2025-01-01T00:00:00Z", completed_at="2025-01-01T00:00:30Z",
        ))
        result = await usecase.execute(ListRunNodeStatusesCommand(run_id="r1"))
        node_a = next(n for n in result.nodes if n["node_id"] == "task-a")
        assert node_a["status"] == "FAILED"
        assert node_a["error"] == "Worker timeout after 30s"
        assert node_a["completed_at"] == "2025-01-01T00:00:30Z"

    async def test_failed_loop_node_orders_last_error_by_scope_stack_iteration(
        self, usecase, workflow_repo, run_repo, task_repo,
    ):
        """Regression: a FAILED node whose tasks carry loop-iteration context
        must not 500. ``Task`` has no ``iteration_index`` scalar — loop
        iteration lives in ``scope_stack`` — so ``_last_error`` orders by
        ``scope_stack[-1].iter``. A bare ``t.iteration_index`` previously raised
        ``AttributeError`` and 500'd this endpoint for every run with a FAILED
        node. Iterations are inserted in reverse and share timestamps so the
        iteration key (not insertion order) decides the 'last' error."""
        wf = Workflow(
            id="wf-loop", name="Loop",
            nodes=[WorkflowNode(id="task-a", name="A", node_type=NodeType.TASK, worker_type="w1")],
            edges=[],
        )
        workflow_repo.save(wf)
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf-loop", status=RunStatus.FAILED))

        def scope(i: int) -> list[LoopScope]:
            return [LoopScope(loop_node_id="loop", invocation_id="inv", iter=i, total=2)]

        # Insert iteration 1 BEFORE iteration 0, same timestamps → only the
        # scope_stack iteration key yields the correct 'last error' = iter 1.
        task_repo.save(Task(
            id="t-i1", run_id="r1", node_id="task-a", status=TaskStatus.FAILED,
            error="iter-1 failed", scope_stack=scope(1),
            started_at="2025-01-01T00:00:00Z", completed_at="2025-01-01T00:00:10Z",
        ))
        task_repo.save(Task(
            id="t-i0", run_id="r1", node_id="task-a", status=TaskStatus.FAILED,
            error="iter-0 failed", scope_stack=scope(0),
            started_at="2025-01-01T00:00:00Z", completed_at="2025-01-01T00:00:10Z",
        ))

        result = await usecase.execute(ListRunNodeStatusesCommand(run_id="r1"))
        assert result.error is None  # endpoint did not 500
        node_a = next(n for n in result.nodes if n["node_id"] == "task-a")
        assert node_a["status"] == "FAILED"
        assert node_a["error"] == "iter-1 failed"  # highest iteration's error

    async def test_skipped_node(
        self, usecase, workflow_repo, run_repo, workflow_with_subflow,
    ):
        workflow_repo.save(workflow_with_subflow)
        run_repo.save(WorkflowRun(
            id="r1", workflow_id="wf-1", status=RunStatus.RUNNING,
            context=RunContext(skipped_nodes={"task-b"}),
        ))
        result = await usecase.execute(ListRunNodeStatusesCommand(run_id="r1"))
        node_b = next(n for n in result.nodes if n["node_id"] == "task-b")
        assert node_b["status"] == "SKIPPED"

    async def test_workflow_node_exposes_child_run_id(
        self, usecase, workflow_repo, run_repo, workflow_with_subflow,
    ):
        workflow_repo.save(workflow_with_subflow)
        run_repo.save(WorkflowRun(
            id="r1", workflow_id="wf-1", status=RunStatus.RUNNING,
            context=RunContext(child_runs={"subflow": "child-abc"}),
        ))
        result = await usecase.execute(ListRunNodeStatusesCommand(run_id="r1"))
        subflow = next(n for n in result.nodes if n["node_id"] == "subflow")
        assert subflow["node_type"] == "WORKFLOW"
        assert subflow["child_run_id"] == "child-abc"

    async def test_node_types_are_string_serialised(
        self, usecase, workflow_repo, run_repo, workflow_with_subflow,
    ):
        workflow_repo.save(workflow_with_subflow)
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING))
        result = await usecase.execute(ListRunNodeStatusesCommand(run_id="r1"))
        types = {n["node_type"] for n in result.nodes}
        assert types == {"TRIGGER", "TASK", "WORKFLOW"}
