"""Tests for GetNodeExecutionUseCase."""

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext
from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects import RunStatus, TaskStatus, NodeType
from app.layer2_application.features.get_node_execution.use_cases.get_node_execution_use_case import (
    GetNodeExecutionUseCase,
    GetNodeExecutionCommand,
)
from tests.conftest import DummyLogger


@pytest.fixture
def usecase(workflow_repo, run_repo, task_repo):
    return GetNodeExecutionUseCase(
        logger=DummyLogger(),
        run_repo=run_repo,
        task_repo=task_repo,
        workflow_repo=workflow_repo,
    )


@pytest.fixture
def workflow_with_csv_node():
    return Workflow(
        id="wf-csv",
        name="CSV Workflow",
        nodes=[
            WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="process",
                name="Process Orders",
                node_type=NodeType.TASK,
                worker_type="csv-worker",
                definition_snapshot={
                    "input_schema": [{"key": "file_id", "type": "string", "label": "File"}],
                    "edit_mode": "before_dispatch",
                    "data_mode": "csv_multi",
                },
            ),
            WorkflowNode(id="done", name="Done", node_type=NodeType.TASK, worker_type="w2"),
        ],
        edges=[
            WorkflowEdge(source_node_id="trigger", target_node_id="process"),
            WorkflowEdge(source_node_id="process", target_node_id="done"),
        ],
    )


@pytest.fixture
def running_run():
    return WorkflowRun(
        id="run-1",
        workflow_id="wf-csv",
        status=RunStatus.RUNNING,
        context=RunContext(
            variables={
                "process": {"output": {"uri": "s3://bucket/out.csv"}, "metadata": {"rows": 42}},
            },
        ),
    )


class TestGetNodeExecution:
    async def test_run_not_found(self, usecase):
        result = await usecase.execute(GetNodeExecutionCommand(run_id="missing", node_id="x"))
        assert result.error == "Run not found"

    async def test_workflow_not_found(self, usecase, run_repo):
        run = WorkflowRun(id="run-x", workflow_id="no-wf", status=RunStatus.RUNNING)
        run_repo.save(run)
        result = await usecase.execute(GetNodeExecutionCommand(run_id="run-x", node_id="n"))
        assert result.error == "Workflow not found"

    async def test_node_not_found(self, usecase, workflow_repo, run_repo, workflow_with_csv_node, running_run):
        workflow_repo.save(workflow_with_csv_node)
        run_repo.save(running_run)
        result = await usecase.execute(GetNodeExecutionCommand(run_id="run-1", node_id="nonexistent"))
        assert result.error == "Node not found"

    async def test_completed_task_returns_execution(
        self, usecase, workflow_repo, run_repo, task_repo, workflow_with_csv_node, running_run,
    ):
        workflow_repo.save(workflow_with_csv_node)
        run_repo.save(running_run)
        task_repo.save(Task(
            id="t1", run_id="run-1", node_id="process",
            status=TaskStatus.COMPLETED,
            resolved_input={"file_id": "f-123"},
            output_ref="s3://bucket/out.csv",
            output_metadata={"rows": 42},
            started_at="2025-01-01T00:00:00Z",
            completed_at="2025-01-01T00:01:00Z",
        ))

        result = await usecase.execute(GetNodeExecutionCommand(run_id="run-1", node_id="process"))

        assert result.error is None
        assert result.run_id == "run-1"
        assert result.node_id == "process"
        assert result.node_name == "Process Orders"
        assert result.node_type == "TASK"
        assert result.status == "COMPLETED"
        assert result.edit_mode == "before_dispatch"
        assert result.data_mode == "csv_multi"
        assert len(result.executions) == 1
        ex = result.executions[0]
        assert ex["task_id"] == "t1"
        assert ex["status"] == "COMPLETED"
        assert ex["editable"] is False
        assert ex["resolved_input"]["file_id"] == "f-123"

    async def test_pending_task_is_editable(
        self, usecase, workflow_repo, run_repo, task_repo, workflow_with_csv_node, running_run,
    ):
        workflow_repo.save(workflow_with_csv_node)
        run_repo.save(running_run)
        task_repo.save(Task(
            id="t-pending", run_id="run-1", node_id="process",
            status=TaskStatus.PENDING,
            resolved_input={"file_id": "f-abc", "data_mode": "csv_multi"},
        ))

        result = await usecase.execute(GetNodeExecutionCommand(run_id="run-1", node_id="process"))

        assert result.status == "PENDING"
        assert len(result.executions) == 1
        assert result.executions[0]["editable"] is True
        assert result.executions[0]["resolved_input"]["file_id"] == "f-abc"

    async def test_skipped_node(
        self, usecase, workflow_repo, run_repo, workflow_with_csv_node,
    ):
        workflow_repo.save(workflow_with_csv_node)
        run = WorkflowRun(
            id="run-skip", workflow_id="wf-csv", status=RunStatus.RUNNING,
            context=RunContext(skipped_nodes={"process"}),
        )
        run_repo.save(run)

        result = await usecase.execute(GetNodeExecutionCommand(run_id="run-skip", node_id="process"))

        assert result.skipped is True
        assert result.status == "SKIPPED"
        assert result.executions == []

    async def test_context_output_populated(
        self, usecase, workflow_repo, run_repo, task_repo, workflow_with_csv_node, running_run,
    ):
        workflow_repo.save(workflow_with_csv_node)
        run_repo.save(running_run)
        task_repo.save(Task(id="t2", run_id="run-1", node_id="process", status=TaskStatus.COMPLETED))

        result = await usecase.execute(GetNodeExecutionCommand(run_id="run-1", node_id="process"))

        assert result.context_output is not None
        assert result.context_output["output"]["uri"] == "s3://bucket/out.csv"

    async def test_input_schema_from_node_definition(
        self, usecase, workflow_repo, run_repo, task_repo, workflow_with_csv_node, running_run,
    ):
        workflow_repo.save(workflow_with_csv_node)
        run_repo.save(running_run)
        task_repo.save(Task(id="t3", run_id="run-1", node_id="process", status=TaskStatus.PENDING))

        result = await usecase.execute(GetNodeExecutionCommand(run_id="run-1", node_id="process"))

        assert len(result.input_schema) == 1
        assert result.input_schema[0]["key"] == "file_id"

    async def test_multiple_tasks_sorted_by_iteration(
        self, usecase, workflow_repo, run_repo, task_repo, workflow_with_csv_node, running_run,
    ):
        workflow_repo.save(workflow_with_csv_node)
        run_repo.save(running_run)
        from app.layer1_domain.value_objects.loop_scope import LoopScope
        t2 = Task(id="t-iter-2", run_id="run-1", node_id="process", status=TaskStatus.COMPLETED)
        t2.scope_stack = [LoopScope(loop_node_id="loop", invocation_id="inv", iter=2, total=3)]
        t0 = Task(id="t-iter-0", run_id="run-1", node_id="process", status=TaskStatus.COMPLETED)
        t0.scope_stack = [LoopScope(loop_node_id="loop", invocation_id="inv", iter=0, total=3)]
        t1 = Task(id="t-iter-1", run_id="run-1", node_id="process", status=TaskStatus.RUNNING)
        t1.scope_stack = [LoopScope(loop_node_id="loop", invocation_id="inv", iter=1, total=3)]
        task_repo.save(t2)
        task_repo.save(t0)
        task_repo.save(t1)

        result = await usecase.execute(GetNodeExecutionCommand(run_id="run-1", node_id="process"))

        assert len(result.executions) == 3
        assert result.executions[0]["task_id"] == "t-iter-0"
        assert result.executions[1]["task_id"] == "t-iter-1"
        assert result.executions[2]["task_id"] == "t-iter-2"
        assert result.status == "RUNNING"

    async def test_node_with_no_tasks_in_flight_run_is_not_started(
        self, usecase, workflow_repo, run_repo, workflow_with_csv_node, running_run,
    ):
        workflow_repo.save(workflow_with_csv_node)
        run_repo.save(running_run)

        result = await usecase.execute(GetNodeExecutionCommand(run_id="run-1", node_id="process"))

        # Run is RUNNING but this node has no tasks yet — must NOT inherit RUNNING
        # (would falsely animate the node as executing in the canvas).
        assert result.status == "NOT_STARTED"
        assert result.executions == []
