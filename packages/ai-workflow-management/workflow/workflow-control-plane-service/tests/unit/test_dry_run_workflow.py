"""Unit tests for the DryRunWorkflow use case."""

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from app.layer2_application.features.dry_run_workflow.use_cases.dry_run_workflow_use_case import (
    DryRunWorkflowUseCase,
    DryRunWorkflowCommand,
)


class DummyLogger:
    def __init__(self):
        self.messages = []

    def info(self, message, *args, **kwargs):
        self.messages.append(("INFO", message))

    def error(self, message, *args, **kwargs):
        self.messages.append(("ERROR", message))

    def warning(self, message, *args, **kwargs):
        self.messages.append(("WARNING", message))

    def debug(self, message, *args, **kwargs):
        self.messages.append(("DEBUG", message))

    def log(self, level, message, *args, **kwargs):
        self.messages.append((str(level), message))


@pytest.fixture
def logger():
    return DummyLogger()


@pytest.fixture
def workflow_repo():
    return MemoryWorkflowRepository()


@pytest.fixture
def usecase(logger, workflow_repo):
    return DryRunWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)


class TestDryRunWorkflowNotFound:
    @pytest.mark.asyncio
    async def test_workflow_not_found(self, usecase):
        result = await usecase.execute(DryRunWorkflowCommand(workflow_id="missing"))
        assert result.error == "Workflow not found"


class TestDryRunSimpleTriggerTask:
    @pytest.mark.asyncio
    async def test_simple_trigger_task(self, usecase, workflow_repo):
        wf = Workflow(
            id="wf-simple",
            name="Simple Workflow",
            nodes=[
                WorkflowNode(id="n1", name="Start", node_type=NodeType.TRIGGER),
                WorkflowNode(id="n2", name="DoWork", node_type=NodeType.TASK, worker_type="w1"),
            ],
            edges=[
                WorkflowEdge(source_node_id="n1", target_node_id="n2"),
            ],
        )
        workflow_repo.save(wf)
        result = await usecase.execute(DryRunWorkflowCommand(workflow_id="wf-simple"))
        assert result.is_valid is True
        assert len(result.steps) == 2
        assert result.steps[0]["node_type"] == "TRIGGER"
        assert result.steps[1]["node_type"] == "TASK"
        assert result.steps[1]["worker_type"] == "w1"


class TestDryRunSchemaValidation:
    @pytest.mark.asyncio
    async def test_schema_valid_input(self, usecase, workflow_repo):
        wf = Workflow(
            id="wf-schema",
            name="Schema WF",
            input_schema=[
                {"key": "name", "rules": {"isRequired": True}, "fieldConfig": {"label": "Name"}},
            ],
            nodes=[
                WorkflowNode(id="n1", name="Start", node_type=NodeType.TRIGGER),
            ],
            edges=[],
        )
        workflow_repo.save(wf)
        result = await usecase.execute(
            DryRunWorkflowCommand(workflow_id="wf-schema", input_data={"name": "test"})
        )
        assert result.schema_errors == []
        assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_schema_missing_required(self, usecase, workflow_repo):
        wf = Workflow(
            id="wf-schema2",
            name="Schema WF 2",
            input_schema=[
                {"key": "name", "rules": {"isRequired": True}, "fieldConfig": {"label": "Name"}},
            ],
            nodes=[
                WorkflowNode(id="n1", name="Start", node_type=NodeType.TRIGGER),
            ],
            edges=[],
        )
        workflow_repo.save(wf)
        result = await usecase.execute(
            DryRunWorkflowCommand(workflow_id="wf-schema2", input_data={})
        )
        assert result.is_valid is False
        assert len(result.schema_errors) > 0

    @pytest.mark.asyncio
    async def test_schema_no_input_data_warning(self, usecase, workflow_repo):
        wf = Workflow(
            id="wf-schema3",
            name="Schema WF 3",
            input_schema=[
                {"key": "name", "rules": {"isRequired": True}, "fieldConfig": {"label": "Name"}},
            ],
            nodes=[
                WorkflowNode(id="n1", name="Start", node_type=NodeType.TRIGGER),
            ],
            edges=[],
        )
        workflow_repo.save(wf)
        result = await usecase.execute(
            DryRunWorkflowCommand(workflow_id="wf-schema3")
        )
        assert result.is_valid is True
        assert any("schema validation skipped" in w for w in result.warnings)


class TestDryRunConditionNode:
    @pytest.mark.asyncio
    async def test_condition_node(self, usecase, workflow_repo):
        wf = Workflow(
            id="wf-cond",
            name="Condition WF",
            nodes=[
                WorkflowNode(id="t1", name="Start", node_type=NodeType.TRIGGER),
                WorkflowNode(
                    id="c1", name="Check", node_type=NodeType.CONDITION,
                    config={"expression": "{{$input.x}} > 10"},
                ),
                WorkflowNode(id="a1", name="TaskA", node_type=NodeType.TASK, worker_type="w1"),
                WorkflowNode(id="b1", name="TaskB", node_type=NodeType.TASK, worker_type="w2"),
            ],
            edges=[
                WorkflowEdge(source_node_id="t1", target_node_id="c1"),
                WorkflowEdge(source_node_id="c1", target_node_id="a1", condition="true"),
                WorkflowEdge(source_node_id="c1", target_node_id="b1", condition="false"),
            ],
        )
        workflow_repo.save(wf)
        result = await usecase.execute(DryRunWorkflowCommand(workflow_id="wf-cond"))
        assert result.is_valid is True
        condition_steps = [s for s in result.steps if s["node_type"] == "CONDITION"]
        assert len(condition_steps) == 1
        assert "expression" in condition_steps[0]["config_summary"]
        assert any("runtime evaluation" in w for w in condition_steps[0]["warnings"])


class TestDryRunVariableResolution:
    @pytest.mark.asyncio
    async def test_task_input_resolution_from_input(self, usecase, workflow_repo):
        wf = Workflow(
            id="wf-resolve",
            name="Resolve WF",
            nodes=[
                WorkflowNode(id="n1", name="Start", node_type=NodeType.TRIGGER),
                WorkflowNode(
                    id="n2", name="Task1", node_type=NodeType.TASK, worker_type="w1",
                    input_binding={"x": "{{$input.val}}"},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="n1", target_node_id="n2"),
            ],
        )
        workflow_repo.save(wf)
        result = await usecase.execute(
            DryRunWorkflowCommand(workflow_id="wf-resolve", input_data={"val": 42})
        )
        assert result.is_valid is True
        task_step = result.steps[1]
        assert task_step["resolved_input"] == {"x": 42}

    @pytest.mark.asyncio
    async def test_task_input_resolution_upstream_fails(self, usecase, workflow_repo):
        wf = Workflow(
            id="wf-resolve2",
            name="Resolve WF 2",
            nodes=[
                WorkflowNode(id="n1", name="Start", node_type=NodeType.TRIGGER),
                WorkflowNode(id="n2", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
                WorkflowNode(
                    id="n3", name="Task2", node_type=NodeType.TASK, worker_type="w2",
                    input_binding={"x": "{{$n2.output.x}}"},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="n1", target_node_id="n2"),
                WorkflowEdge(source_node_id="n2", target_node_id="n3"),
            ],
        )
        workflow_repo.save(wf)
        result = await usecase.execute(
            DryRunWorkflowCommand(workflow_id="wf-resolve2")
        )
        assert result.is_valid is True
        task_step = result.steps[2]  # n3 is the third step
        assert task_step["resolved_input"] is None
        assert any("runtime" in w for w in task_step["warnings"])


class TestDryRunTaskMissingWorkerId:
    @pytest.mark.asyncio
    async def test_task_missing_worker_id(self, usecase, workflow_repo):
        wf = Workflow(
            id="wf-noworker",
            name="No Worker WF",
            nodes=[
                WorkflowNode(id="n1", name="Start", node_type=NodeType.TRIGGER),
                WorkflowNode(id="n2", name="Task1", node_type=NodeType.TASK),
            ],
            edges=[
                WorkflowEdge(source_node_id="n1", target_node_id="n2"),
            ],
        )
        workflow_repo.save(wf)
        result = await usecase.execute(DryRunWorkflowCommand(workflow_id="wf-noworker"))
        assert result.is_valid is True
        task_step = result.steps[1]
        assert any("Missing worker_type" in w for w in task_step["warnings"])


class TestDryRunSummaryCounts:
    @pytest.mark.asyncio
    async def test_summary_counts(self, usecase, workflow_repo):
        wf = Workflow(
            id="wf-summary",
            name="Summary WF",
            nodes=[
                WorkflowNode(id="t1", name="Trigger", node_type=NodeType.TRIGGER),
                WorkflowNode(id="n1", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
                WorkflowNode(id="n2", name="Task2", node_type=NodeType.TASK, worker_type="w2"),
                WorkflowNode(id="n3", name="Task3", node_type=NodeType.TASK, worker_type="w3"),
            ],
            edges=[
                WorkflowEdge(source_node_id="t1", target_node_id="n1"),
                WorkflowEdge(source_node_id="n1", target_node_id="n2"),
                WorkflowEdge(source_node_id="n2", target_node_id="n3"),
            ],
        )
        workflow_repo.save(wf)
        result = await usecase.execute(DryRunWorkflowCommand(workflow_id="wf-summary"))
        assert result.is_valid is True
        summary = result.summary
        assert summary["total_nodes"] == 4
        assert summary["total_edges"] == 3
        assert summary["task_nodes_count"] == 3
        assert "t1" in summary["entry_nodes"]
        assert "n3" in summary["leaf_nodes"]
        assert summary["node_type_counts"]["TRIGGER"] == 1
        assert summary["node_type_counts"]["TASK"] == 3
        assert summary["has_loops"] is False
        assert summary["has_conditions"] is False
        assert summary["has_parallel"] is False
        assert summary["has_sub_workflows"] is False


class TestDryRunComplexWorkflow:
    @pytest.mark.asyncio
    async def test_complex_workflow(self, usecase, workflow_repo):
        wf = Workflow(
            id="wf-complex",
            name="Complex WF",
            nodes=[
                WorkflowNode(id="t1", name="Trigger", node_type=NodeType.TRIGGER),
                WorkflowNode(id="p1", name="FanOut", node_type=NodeType.PARALLEL),
                WorkflowNode(id="a1", name="TaskA", node_type=NodeType.TASK, worker_type="w1"),
                WorkflowNode(id="b1", name="TaskB", node_type=NodeType.TASK, worker_type="w2"),
                WorkflowNode(id="m1", name="Merge", node_type=NodeType.MERGE),
                WorkflowNode(id="c1", name="TaskC", node_type=NodeType.TASK, worker_type="w3"),
            ],
            edges=[
                WorkflowEdge(source_node_id="t1", target_node_id="p1"),
                WorkflowEdge(source_node_id="p1", target_node_id="a1"),
                WorkflowEdge(source_node_id="p1", target_node_id="b1"),
                WorkflowEdge(source_node_id="a1", target_node_id="m1"),
                WorkflowEdge(source_node_id="b1", target_node_id="m1"),
                WorkflowEdge(source_node_id="m1", target_node_id="c1"),
            ],
        )
        workflow_repo.save(wf)
        result = await usecase.execute(DryRunWorkflowCommand(workflow_id="wf-complex"))
        assert result.is_valid is True
        assert len(result.steps) == 6
        # Trigger must come first
        assert result.steps[0]["node_type"] == "TRIGGER"
        # Parallel must come before its children
        parallel_order = next(s["order"] for s in result.steps if s["node_type"] == "PARALLEL")
        task_a_order = next(s["order"] for s in result.steps if s["node_id"] == "a1")
        task_b_order = next(s["order"] for s in result.steps if s["node_id"] == "b1")
        merge_order = next(s["order"] for s in result.steps if s["node_type"] == "MERGE")
        task_c_order = next(s["order"] for s in result.steps if s["node_id"] == "c1")
        assert parallel_order < task_a_order
        assert parallel_order < task_b_order
        assert merge_order > task_a_order
        assert merge_order > task_b_order
        assert task_c_order > merge_order
        assert result.summary["has_parallel"] is True
