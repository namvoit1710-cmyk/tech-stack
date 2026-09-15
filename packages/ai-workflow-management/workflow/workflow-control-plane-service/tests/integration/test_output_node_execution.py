"""Integration tests for OUTPUT node handling through OrchestrationEngine.

Tests the OUTPUT node handler via the full orchestration flow, verifying
that state_updates are properly applied and the node completes immediately.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import GraphTopology, RunContext, WorkflowRun
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
from app.layer2_application.node_handlers.output_handler import OutputNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer2_application.orchestration_queue.memory_queue import MemoryOrchestrationQueue
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from tests.helpers.event_capture import CapturingEventPublisher


class _DummyLogger:
    def info(self, *a, **kw): ...
    def warning(self, *a, **kw): ...
    def error(self, *a, **kw): ...
    def debug(self, *a, **kw): ...


def _build_trigger_compute_output_workflow() -> Workflow:
    """TRIGGER → COMPUTE → OUTPUT workflow.
    
    Tests the OUTPUT node receiving data from COMPUTE and producing
    workflow output based on output_schema.
    """
    trigger = WorkflowNode(
        id="trigger-1",
        name="trigger",
        node_type=NodeType.TRIGGER,
        config={},
    )
    compute = WorkflowNode(
        id="compute-1",
        name="calculate",
        node_type=NodeType.COMPUTE,
        config={"expression": "10 * 5"},
    )
    output = WorkflowNode(
        id="output-1",
        name="final_output",
        node_type=NodeType.OUTPUT,
        input_binding={
            "result": "{{$compute-1.output.result}}",
            "message": "Calculation complete",
        },
        config={
            "output_schema": [
                {"key": "result", "type": "number", "label": "Result"},
                {"key": "message", "type": "string", "label": "Message"},
            ],
        },
    )
    return Workflow(
        id="wf-output-test-1",
        name="output-integration-test",
        version="1",
        nodes=[trigger, compute, output],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="compute-1"),
            WorkflowEdge(id="e2", source_node_id="compute-1", target_node_id="output-1"),
        ],
    )


def _build_simple_output_workflow() -> Workflow:
    """TRIGGER → OUTPUT workflow with direct trigger data binding."""
    trigger = WorkflowNode(
        id="trigger-1",
        name="trigger",
        node_type=NodeType.TRIGGER,
        config={},
    )
    output = WorkflowNode(
        id="output-1",
        name="simple_output",
        node_type=NodeType.OUTPUT,
        input_binding={
            "name": "{{$trigger.body.name}}",
            "age": "{{$trigger.body.age}}",
        },
        config={
            "output_schema": [
                {"key": "name", "type": "string"},
                {"key": "age", "type": "number"},
            ],
        },
    )
    return Workflow(
        id="wf-simple-output-1",
        name="simple-output-test",
        version="1",
        nodes=[trigger, output],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="output-1"),
        ],
    )


@pytest.fixture
def engine_deps():
    """Build OrchestrationEngine with all required dependencies."""
    file_client = MockFileServiceClient()
    csv_converter = CsvConverter()
    data_store = DataStoreService(file_client, csv_converter)

    resolver = VariableResolver(
        strategies=[
            FileBackedResolverStrategy(data_store, file_client=file_client),
            MetadataResolverStrategy(),
            TraversalResolverStrategy(),
        ],
    )

    handler_registry = NodeHandlerRegistry()
    handler_registry.register(NodeType.TRIGGER, TriggerNodeHandler())
    handler_registry.register(NodeType.COMPUTE, ComputeNodeHandler(resolver))
    handler_registry.register(NodeType.OUTPUT, OutputNodeHandler(resolver, data_store))

    task_factory = TaskFactory()
    successor_processor = SuccessorProcessor(
        handler_registry, task_factory, data_store_service=data_store,
    )

    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    workflow_repo = MemoryWorkflowRepository()

    stage_recorder = StageRecorder(MagicMock())
    stage_recorder.record = MagicMock()

    event_publisher = CapturingEventPublisher()
    engine = OrchestrationEngine(
        handler_registry=handler_registry,
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

    queue = MemoryOrchestrationQueue()
    engine.set_queue(queue)

    return {
        "engine": engine,
        "queue": queue,
        "workflow_repo": workflow_repo,
        "run_repo": run_repo,
        "task_repo": task_repo,
        "task_factory": task_factory,
        "data_store": data_store,
        "file_client": file_client,
        "event_publisher": event_publisher,
    }


class TestOutputNodeExecution:
    """Tests OUTPUT node execution through the orchestration engine."""

    @pytest.mark.asyncio
    async def test_output_node_produces_state_updates(self, engine_deps):
        """OUTPUT node's state_updates are applied via orchestration."""
        deps = engine_deps
        engine = deps["engine"]

        workflow = _build_simple_output_workflow()
        deps["workflow_repo"].save(workflow)

        topology = GraphTopology.from_workflow(workflow)
        run = WorkflowRun(
            id="run-output-1",
            workflow_id=workflow.id,
            status=RunStatus.RUNNING,
            context=RunContext(
                completed_nodes={"trigger-1"},
                variables={
                    "trigger": {"body": {"name": "John Doe", "age": "30"}},
                },
                topology=topology,
            ),
        )
        deps["run_repo"].save(run)

        output_node = workflow.get_node("output-1")
        output_task = deps["task_factory"].create_task(output_node, run)
        deps["task_repo"].save(output_task)

        # Execute the OUTPUT node
        await engine.orchestrate_execute_node(run.id, output_task.id)

        # Verify state_updates were emitted via NodeOutputProduced
        projected = deps["event_publisher"].projected_variables()
        assert "output-1" in projected, (
            "OUTPUT node state_updates should be in NodeOutputProduced write-set"
        )

        output_data = projected["output-1"]
        assert is_file_ref(output_data), "Output should be stored as file_ref"

        # Verify the resolved values with type coercion (wire-thin refs carry
        # no extracted cache — materialize the content instead).
        materialized = await deps["data_store"].materialize(output_data)
        fields = materialized.get("output", {})
        assert fields.get("name") == "John Doe"
        assert fields.get("age") == 30

    @pytest.mark.asyncio
    async def test_output_node_completes_immediately(self, engine_deps):
        """OUTPUT node marks task COMPLETED without worker dispatch."""
        deps = engine_deps
        engine = deps["engine"]

        workflow = _build_simple_output_workflow()
        deps["workflow_repo"].save(workflow)

        topology = GraphTopology.from_workflow(workflow)
        run = WorkflowRun(
            id="run-output-2",
            workflow_id=workflow.id,
            status=RunStatus.RUNNING,
            context=RunContext(
                completed_nodes={"trigger-1"},
                variables={
                    "trigger": {"body": {"name": "Alice", "age": "25"}},
                },
                topology=topology,
            ),
        )
        deps["run_repo"].save(run)

        output_node = workflow.get_node("output-1")
        output_task = deps["task_factory"].create_task(output_node, run)
        deps["task_repo"].save(output_task)

        await engine.orchestrate_execute_node(run.id, output_task.id)

        # Task should be COMPLETED
        saved_task = deps["task_repo"].get_by_id(output_task.id)
        assert saved_task.status == TaskStatus.COMPLETED

        # Node should be in completed_nodes
        saved_run = deps["run_repo"].get_by_id(run.id)
        assert "output-1" in saved_run.context.completed_nodes

    @pytest.mark.asyncio
    async def test_output_node_with_upstream_compute(self, engine_deps):
        """OUTPUT node correctly resolves data from upstream COMPUTE node."""
        deps = engine_deps
        engine = deps["engine"]

        workflow = _build_trigger_compute_output_workflow()
        deps["workflow_repo"].save(workflow)

        # Pre-store the compute output as a file_ref (simulating COMPUTE completion)
        compute_output_ref = await deps["data_store"].store_data(
            "compute-1", {"result": 50},
        )

        topology = GraphTopology.from_workflow(workflow)
        run = WorkflowRun(
            id="run-output-3",
            workflow_id=workflow.id,
            status=RunStatus.RUNNING,
            context=RunContext(
                completed_nodes={"trigger-1", "compute-1"},
                variables={
                    "compute-1": compute_output_ref,
                },
                topology=topology,
            ),
        )
        deps["run_repo"].save(run)

        output_node = workflow.get_node("output-1")
        output_task = deps["task_factory"].create_task(output_node, run)
        deps["task_repo"].save(output_task)

        await engine.orchestrate_execute_node(run.id, output_task.id)

        # Verify OUTPUT produced state_updates
        projected = deps["event_publisher"].projected_variables()
        assert "output-1" in projected

        saved_task = deps["task_repo"].get_by_id(output_task.id)
        assert saved_task.status == TaskStatus.COMPLETED


class TestOutputNodeTypeCoercion:
    """Tests OUTPUT node type coercion based on output_schema."""

    @pytest.mark.asyncio
    async def test_string_to_number_coercion(self, engine_deps):
        """OUTPUT node coerces string values to number when schema specifies."""
        deps = engine_deps

        workflow = Workflow(
            id="wf-coerce-1",
            name="coercion-test",
            version="1",
            nodes=[
                WorkflowNode(id="trigger-1", name="t", node_type=NodeType.TRIGGER, config={}),
                WorkflowNode(
                    id="output-1",
                    name="o",
                    node_type=NodeType.OUTPUT,
                    input_binding={"count": "{{$trigger.body.count}}"},
                    config={"output_schema": [{"key": "count", "type": "number"}]},
                ),
            ],
            edges=[
                WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="output-1"),
            ],
        )
        deps["workflow_repo"].save(workflow)

        topology = GraphTopology.from_workflow(workflow)
        run = WorkflowRun(
            id="run-coerce-1",
            workflow_id=workflow.id,
            status=RunStatus.RUNNING,
            context=RunContext(
                completed_nodes={"trigger-1"},
                variables={"trigger": {"body": {"count": "42"}}},
                topology=topology,
            ),
        )
        deps["run_repo"].save(run)

        output_node = workflow.get_node("output-1")
        output_task = deps["task_factory"].create_task(output_node, run)
        deps["task_repo"].save(output_task)

        await deps["engine"].orchestrate_execute_node(run.id, output_task.id)

        projected = deps["event_publisher"].projected_variables()
        assert "output-1" in projected

    @pytest.mark.asyncio
    async def test_string_to_boolean_coercion(self, engine_deps):
        """OUTPUT node coerces string 'true'/'false' to boolean."""
        deps = engine_deps

        workflow = Workflow(
            id="wf-bool-1",
            name="bool-test",
            version="1",
            nodes=[
                WorkflowNode(id="trigger-1", name="t", node_type=NodeType.TRIGGER, config={}),
                WorkflowNode(
                    id="output-1",
                    name="o",
                    node_type=NodeType.OUTPUT,
                    input_binding={"active": "{{$trigger.body.active}}"},
                    config={"output_schema": [{"key": "active", "type": "boolean"}]},
                ),
            ],
            edges=[
                WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="output-1"),
            ],
        )
        deps["workflow_repo"].save(workflow)

        topology = GraphTopology.from_workflow(workflow)
        run = WorkflowRun(
            id="run-bool-1",
            workflow_id=workflow.id,
            status=RunStatus.RUNNING,
            context=RunContext(
                completed_nodes={"trigger-1"},
                variables={"trigger": {"body": {"active": "true"}}},
                topology=topology,
            ),
        )
        deps["run_repo"].save(run)

        output_node = workflow.get_node("output-1")
        output_task = deps["task_factory"].create_task(output_node, run)
        deps["task_repo"].save(output_task)

        await deps["engine"].orchestrate_execute_node(run.id, output_task.id)

        saved_task = deps["task_repo"].get_by_id(output_task.id)
        assert saved_task.status == TaskStatus.COMPLETED


class TestOutputNodeEmptyConfig:
    """Tests OUTPUT node with empty/minimal configuration."""

    @pytest.mark.asyncio
    async def test_empty_config_completes_successfully(self, engine_deps):
        """OUTPUT node with empty config completes without error."""
        deps = engine_deps

        workflow = Workflow(
            id="wf-empty-1",
            name="empty-config-test",
            version="1",
            nodes=[
                WorkflowNode(id="trigger-1", name="t", node_type=NodeType.TRIGGER, config={}),
                WorkflowNode(id="output-1", name="o", node_type=NodeType.OUTPUT, config={}),
            ],
            edges=[
                WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="output-1"),
            ],
        )
        deps["workflow_repo"].save(workflow)

        topology = GraphTopology.from_workflow(workflow)
        run = WorkflowRun(
            id="run-empty-1",
            workflow_id=workflow.id,
            status=RunStatus.RUNNING,
            context=RunContext(
                completed_nodes={"trigger-1"},
                variables={},
                topology=topology,
            ),
        )
        deps["run_repo"].save(run)

        output_node = workflow.get_node("output-1")
        output_task = deps["task_factory"].create_task(output_node, run)
        deps["task_repo"].save(output_task)

        await deps["engine"].orchestrate_execute_node(run.id, output_task.id)

        saved_task = deps["task_repo"].get_by_id(output_task.id)
        assert saved_task.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_no_input_binding_produces_empty_output(self, engine_deps):
        """OUTPUT node without input_binding produces empty state_updates."""
        deps = engine_deps

        workflow = Workflow(
            id="wf-nobind-1",
            name="no-binding-test",
            version="1",
            nodes=[
                WorkflowNode(id="trigger-1", name="t", node_type=NodeType.TRIGGER, config={}),
                WorkflowNode(
                    id="output-1",
                    name="o",
                    node_type=NodeType.OUTPUT,
                    config={
                        "output_schema": [{"key": "unused", "type": "string"}],
                    },
                ),
            ],
            edges=[
                WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="output-1"),
            ],
        )
        deps["workflow_repo"].save(workflow)

        topology = GraphTopology.from_workflow(workflow)
        run = WorkflowRun(
            id="run-nobind-1",
            workflow_id=workflow.id,
            status=RunStatus.RUNNING,
            context=RunContext(
                completed_nodes={"trigger-1"},
                variables={},
                topology=topology,
            ),
        )
        deps["run_repo"].save(run)

        output_node = workflow.get_node("output-1")
        output_task = deps["task_factory"].create_task(output_node, run)
        deps["task_repo"].save(output_task)

        await deps["engine"].orchestrate_execute_node(run.id, output_task.id)

        saved_task = deps["task_repo"].get_by_id(output_task.id)
        assert saved_task.status == TaskStatus.COMPLETED
