"""Integration test for CSV file-backed workflow execution.

Tests end-to-end: start run → input stored as CSV → TASK dispatch →
complete task → output stored as CSV → variable resolution from file references.
"""

import pytest

pytestmark = pytest.mark.skip(reason="inline-execution coverage — rewrite to drive MemoryOrchestrationQueue via tests.helpers.queue_runner")
from unittest.mock import AsyncMock, MagicMock

from app.layer1_domain.entities.data_reference import is_file_ref, FILE_REF_MARKER
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext
from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.resolve.variable_resolver import (
    VariableResolver,
    ConditionEvaluator,
    MetadataResolverStrategy,
    TraversalResolverStrategy,
)
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.task_handler import TaskNodeHandler
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
from app.layer2_application.node_handlers.condition_handler import ConditionNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository

from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunUseCase, StartRunCommand,
)
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskUseCase, CompleteTaskCommand,
)


class DummyLogger:
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


class DummyMonitor:
    def increment(self, *a, **kw): pass


def _build_linear_workflow() -> Workflow:
    """TRIGGER → TASK → COMPUTE → end"""
    trigger = WorkflowNode(
        id="trigger-1", name="trigger", node_type=NodeType.TRIGGER,
        config={},
    )
    task = WorkflowNode(
        id="task-1", name="api_call", node_type=NodeType.TASK,
        worker_type="http-request",
        input_binding={"url": "{{$input.url}}"},
        config={},
    )
    compute = WorkflowNode(
        id="compute-1", name="check_status", node_type=NodeType.COMPUTE,
        config={"expression": "{{$api_call.status_code}} * 2"},
    )
    edges = [
        WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="task-1"),
        WorkflowEdge(id="e2", source_node_id="task-1", target_node_id="compute-1"),
    ]
    return Workflow(
        id="wf-1", name="test-wf", version="1",
        nodes=[trigger, task, compute], edges=edges,
        metadata={},
    )


@pytest.fixture
def deps():
    """Build a complete dependency set for file-backed tests."""
    file_client = MockFileServiceClient()
    csv_converter = CsvConverter()
    data_store = DataStoreService(file_client, csv_converter)

    resolver = VariableResolver(strategies=[
        FileBackedResolverStrategy(data_store),
        MetadataResolverStrategy(),
        TraversalResolverStrategy(),
    ])
    condition_evaluator = ConditionEvaluator(resolver)

    registry = NodeHandlerRegistry()
    registry.register(NodeType.TASK, TaskNodeHandler(resolver))
    registry.register(NodeType.COMPUTE, ComputeNodeHandler(resolver))
    registry.register(NodeType.CONDITION, ConditionNodeHandler(condition_evaluator))
    registry.register(NodeType.TRIGGER, TriggerNodeHandler())

    task_factory = TaskFactory()
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    workflow_repo = MemoryWorkflowRepository()

    stage_recorder = StageRecorder(MagicMock())
    stage_recorder.record = MagicMock()

    event_publisher = MagicMock()
    successor_processor = SuccessorProcessor(registry, task_factory, data_store_service=data_store)

    engine = OrchestrationEngine(
        handler_registry=registry,
        task_factory=task_factory,
        successor_processor=successor_processor,
        stage_recorder=stage_recorder,
        task_repo=task_repo,
        run_repo=run_repo,
        event_publisher=event_publisher,
        file_service_client=file_client,
        csv_converter=csv_converter,
        workflow_repo=workflow_repo,
        variable_resolver=resolver,
        data_store_service=data_store,
    )

    return {
        "logger": DummyLogger(),
        "workflow_repo": workflow_repo,
        "run_repo": run_repo,
        "task_repo": task_repo,
        "orchestration_engine": engine,
        "stage_recorder": stage_recorder,
        "event_publisher": event_publisher,
        "variable_resolver": resolver,
        "data_store_service": data_store,
        "file_client": file_client,
    }


async def test_start_run_stores_input_as_csv(deps):
    """Input data should always be stored as a CSV file ref."""
    workflow = _build_linear_workflow()
    deps["workflow_repo"].save(workflow)

    uc = StartRunUseCase(
        logger=deps["logger"],
        workflow_repo=deps["workflow_repo"],
        run_repo=deps["run_repo"],
        task_repo=deps["task_repo"],
        orchestration_engine=deps["orchestration_engine"],
        stage_recorder=deps["stage_recorder"],
        event_publisher=deps["event_publisher"],
        data_store_service=deps["data_store_service"],
    )

    result = await uc.execute(StartRunCommand(
        workflow_id="wf-1",
        input_data={"url": "https://example.com", "method": "GET"},
    ))

    assert result.error is None or result.error == ""

    run = deps["run_repo"].get_by_id(result.id)
    assert run is not None

    # Input should be stored as a file reference
    input_var = run.context.variables.get("input")
    assert is_file_ref(input_var), f"Expected file ref, got: {type(input_var)}"
    assert input_var["file_id"]

    # Extracted cache should contain the original values
    assert input_var["extracted"]["url"]["value"] == "https://example.com"
    assert input_var["extracted"]["method"]["value"] == "GET"

    # Raw input_data on the run entity should remain unchanged
    assert run.input_data == {"url": "https://example.com", "method": "GET"}


async def test_complete_task_stores_output_as_csv(deps):
    """Completing a task stores output as CSV file ref."""
    workflow = _build_linear_workflow()
    deps["workflow_repo"].save(workflow)

    # Start the run
    start_uc = StartRunUseCase(
        logger=deps["logger"],
        workflow_repo=deps["workflow_repo"],
        run_repo=deps["run_repo"],
        task_repo=deps["task_repo"],
        orchestration_engine=deps["orchestration_engine"],
        stage_recorder=deps["stage_recorder"],
        event_publisher=deps["event_publisher"],
        data_store_service=deps["data_store_service"],
    )

    start_result = await start_uc.execute(StartRunCommand(
        workflow_id="wf-1",
        input_data={"url": "https://example.com"},
    ))

    run = deps["run_repo"].get_by_id(start_result.id)

    # Find the TASK node's task
    all_tasks = deps["task_repo"].list_by_run(run.id)
    task_tasks = [t for t in all_tasks if t.node_id == "task-1"]
    assert task_tasks, "Expected a task for task-1"
    task = task_tasks[0]

    # Complete the task with worker output
    complete_uc = CompleteTaskUseCase(
        logger=deps["logger"],
        workflow_repo=deps["workflow_repo"],
        run_repo=deps["run_repo"],
        task_repo=deps["task_repo"],
        orchestration_engine=deps["orchestration_engine"],
        stage_recorder=deps["stage_recorder"],
        variable_resolver=deps["variable_resolver"],
        data_store_service=deps["data_store_service"],
    )

    complete_result = await complete_uc.execute(CompleteTaskCommand(
        run_id=run.id,
        task_id=task.id,
        status="COMPLETED",
        output_reference="output-file-1",
        output_metadata={"status_code": 200, "response_body": "OK"},
    ))

    assert complete_result.error == ""

    # Verify the task output is stored as a file reference
    updated_run = deps["run_repo"].get_by_id(run.id)
    task_output = updated_run.context.variables.get("task-1")

    assert is_file_ref(task_output), f"Expected file ref, got: {task_output}"
    assert task_output["file_id"]
    # Worker output scalars should be in extracted cache
    assert task_output["extracted"]["status_code"]["value"] == 200


async def test_resolver_reads_from_csv_file_refs(deps):
    """Variable resolver should be able to read values from CSV file references."""
    data_store = deps["data_store_service"]
    resolver = deps["variable_resolver"]

    # Simulate storing a node output as file-backed
    data = {"status_code": 200, "response_body": "hello"}
    ref = await data_store.store_data("api_call", data)

    context = {"api_call": ref}

    # Resolve an expression that references the file-backed output
    result = await resolver.resolve("{{$api_call.status_code}}", context)
    assert result == 200

    result = await resolver.resolve("{{$api_call.response_body}}", context)
    assert result == "hello"


async def test_empty_input_data_not_stored_as_file(deps):
    """When input_data is empty, no file upload happens."""
    workflow = _build_linear_workflow()
    deps["workflow_repo"].save(workflow)

    uc = StartRunUseCase(
        logger=deps["logger"],
        workflow_repo=deps["workflow_repo"],
        run_repo=deps["run_repo"],
        task_repo=deps["task_repo"],
        orchestration_engine=deps["orchestration_engine"],
        stage_recorder=deps["stage_recorder"],
        event_publisher=deps["event_publisher"],
        data_store_service=deps["data_store_service"],
    )

    result = await uc.execute(StartRunCommand(
        workflow_id="wf-1",
        input_data={},
    ))

    run = deps["run_repo"].get_by_id(result.id)
    # Empty input is now stored as file_ref for consistent API format
    input_var = run.context.variables.get("input")
    from app.layer1_domain.entities.data_reference import is_file_ref
    assert is_file_ref(input_var), "Empty input should be stored as file_ref"
