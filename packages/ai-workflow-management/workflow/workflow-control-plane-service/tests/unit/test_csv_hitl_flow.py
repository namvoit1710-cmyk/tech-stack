"""Test the full TRIGGER → TASK(csv_multi, before_dispatch) flow.

Validates that:
1. TaskNodeHandler resolves input_binding even when the task was created by TriggerHandler
2. _apply_csv_data_mode converts the array data to CSV and uploads it
3. Task ends up PENDING with a file_id in resolved_input
"""

import pytest

pytestmark = pytest.mark.skip(reason="legacy inline-execution coverage — rewrite to use tests.helpers.queue_runner for queued-mode regression coverage")

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext
from app.layer1_domain.value_objects import RunStatus, TaskStatus, NodeType
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunUseCase, StartRunCommand,
)
from tests.conftest import DummyLogger, DummyMonitor, DummyEventPublisher, DummyStageRecorder


@pytest.fixture
def deps(workflow_repo, run_repo, task_repo, stage_history_repo, mock_file_service_client):
    """Build full orchestration stack for integration testing."""
    from app.layer2_application.resolve.variable_resolver import (
        VariableResolver, MetadataResolverStrategy, TraversalResolverStrategy,
    )
    from app.layer2_application.events.stage_recorder import StageRecorder
    from app.layer2_application.orchestration.task_factory import TaskFactory
    from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
    from app.layer2_application.data.csv_converter import CsvConverter
    from app.layer2_application.node_handlers.base import NodeHandlerRegistry
    from app.layer2_application.node_handlers.task_handler import TaskNodeHandler
    from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
    from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine

    resolver = VariableResolver(strategies=[MetadataResolverStrategy(), TraversalResolverStrategy()])
    handler_registry = NodeHandlerRegistry()
    handler_registry.register(NodeType.TRIGGER, TriggerNodeHandler())
    handler_registry.register(NodeType.TASK, TaskNodeHandler(resolver))

    task_factory = TaskFactory()
    stage_recorder = StageRecorder(stage_history_repo)
    successor_processor = SuccessorProcessor(handler_registry, task_factory)
    event_publisher = DummyEventPublisher()

    engine = OrchestrationEngine(
        handler_registry=handler_registry,
        task_factory=task_factory,
        successor_processor=successor_processor,
        stage_recorder=stage_recorder,
        task_repo=task_repo,
        run_repo=run_repo,
        event_publisher=event_publisher,
        file_service_client=mock_file_service_client,
        csv_converter=CsvConverter(),
    )

    usecase = StartRunUseCase(
        logger=DummyLogger(),
        workflow_repo=workflow_repo,
        run_repo=run_repo,
        task_repo=task_repo,
        orchestration_engine=engine,
        stage_recorder=stage_recorder,
        event_publisher=event_publisher,
    )

    return {
        "usecase": usecase,
        "workflow_repo": workflow_repo,
        "run_repo": run_repo,
        "task_repo": task_repo,
        "file_client": mock_file_service_client,
    }


class TestCsvHitlFlow:
    async def test_trigger_to_csv_task_produces_pending_with_file_id(self, deps):
        """TRIGGER → TASK(csv_multi, before_dispatch) should create a PENDING task with file_id."""
        workflow = Workflow(
            id="wf-csv",
            name="CSV HITL Workflow",
            nodes=[
                WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
                WorkflowNode(
                    id="csv-task", name="Process", node_type=NodeType.TASK,
                    worker_type="test-worker",
                    input_binding={
                        "employees": [
                            {"id": "E1", "name": "Alice", "salary": "90000"},
                            {"id": "E2", "name": "Bob", "salary": "80000"},
                        ]
                    },
                    config={
                        "_node_definition": {
                            "input_schema": [
                                {"key": "id", "type": "string"},
                                {"key": "name", "type": "string"},
                                {"key": "salary", "type": "number"},
                            ],
                            "data_mode": "csv_multi",
                            "edit_mode": "before_dispatch",
                        },
                        "_node_internals": {
                            "locked_defaults": {"region": "US"},
                            "input_mapping": {},
                            "output_mapping": {},
                            "base_worker_id": "test-worker",
                        },
                    },
                ),
            ],
            edges=[WorkflowEdge(source_node_id="trigger", target_node_id="csv-task")],
        )
        deps["workflow_repo"].save(workflow)

        result = await deps["usecase"].execute(StartRunCommand(workflow_id="wf-csv"))

        assert result.status == "PENDING", f"Expected PENDING, got {result.status}: {result.error}"

        # Find the csv-task
        tasks = deps["task_repo"].list_by_run(result.id)
        csv_tasks = [t for t in tasks if t.node_id == "csv-task"]
        assert len(csv_tasks) == 1, f"Expected 1 csv-task, got {len(csv_tasks)}"

        task = csv_tasks[0]
        assert task.status == TaskStatus.PENDING, f"Expected PENDING, got {task.status}"
        assert task.resolved_input is not None, "resolved_input should not be None"
        assert "file_id" in task.resolved_input, f"Expected file_id in resolved_input: {task.resolved_input}"
        assert task.resolved_input.get("data_mode") == "csv_multi"
        assert task.resolved_input.get("region") == "US"  # locked_default preserved

        # Verify the file was actually uploaded
        file_id = task.resolved_input["file_id"]
        content = await deps["file_client"].download_file(file_id)
        assert content is not None, "File should exist in mock store"
        csv_text = content.decode("utf-8")
        assert "Alice" in csv_text
        assert "Bob" in csv_text

    async def test_trigger_to_plain_task_still_works(self, deps):
        """TRIGGER → TASK(json, no node_definition) should dispatch normally."""
        workflow = Workflow(
            id="wf-json",
            name="Plain Workflow",
            nodes=[
                WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
                WorkflowNode(
                    id="plain-task", name="Process", node_type=NodeType.TASK,
                    worker_type="test-worker",
                    input_binding={"key": "static_value"},
                ),
            ],
            edges=[WorkflowEdge(source_node_id="trigger", target_node_id="plain-task")],
        )
        deps["workflow_repo"].save(workflow)

        result = await deps["usecase"].execute(StartRunCommand(workflow_id="wf-json"))

        assert result.status == "RUNNING"
        tasks = deps["task_repo"].list_by_run(result.id)
        plain_tasks = [t for t in tasks if t.node_id == "plain-task"]
        assert len(plain_tasks) == 1
        task = plain_tasks[0]
        assert task.status == TaskStatus.RUNNING
        assert task.resolved_input == {"key": "static_value"}

    async def test_trigger_to_task_no_input_binding_dispatches_normally(self, deps):
        """TRIGGER → TASK with no input_binding should dispatch with None resolved_input."""
        workflow = Workflow(
            id="wf-empty",
            name="Empty Input Workflow",
            nodes=[
                WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
                WorkflowNode(id="task", name="Task", node_type=NodeType.TASK, worker_type="w1"),
            ],
            edges=[WorkflowEdge(source_node_id="trigger", target_node_id="task")],
        )
        deps["workflow_repo"].save(workflow)

        result = await deps["usecase"].execute(StartRunCommand(workflow_id="wf-empty"))

        assert result.status == "RUNNING"
        tasks = deps["task_repo"].list_by_run(result.id)
        task_nodes = [t for t in tasks if t.node_id == "task"]
        assert len(task_nodes) == 1
        assert task_nodes[0].status == TaskStatus.RUNNING
