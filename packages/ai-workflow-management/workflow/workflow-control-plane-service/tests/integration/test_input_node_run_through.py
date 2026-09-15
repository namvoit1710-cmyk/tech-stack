"""Integration test for INPUT node `runThrough` config.

Pins down the contract: when an INPUT node has `runThrough=true`, the
workflow does NOT pause at it. The node's task is marked COMPLETED inline
and the engine continues to the successor. The `input.requested` event is
still emitted so subscribers (frontend, observers) can react.
"""

from __future__ import annotations

import asyncio

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus

from app.layer2_application.resolve.variable_resolver import (
    VariableResolver,
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
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.node_handlers.input_handler import InputNodeHandler
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler

from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import MemoryStageHistoryRepository

from app.layer2_application.orchestration_queue.memory_queue import (
    MemoryOrchestrationQueue,
)
from app.layer4_frameworks.orchestration_queue.worker import OrchestrationWorker
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer4_frameworks.messaging.event_log_appending_publisher import (
    EventLogAppendingPublisher,
)
from app.layer4_frameworks.run_context.cached_projector_view_store import (
    CachedProjectorRunContextViewStore,
)
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunUseCase, StartRunCommand,
)


class _DummyLogger:
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


class _CapturingEventPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def publish(self, topic, event) -> None:
        self.events.append((topic, event))

    def topics(self) -> list[str]:
        return [t for t, _ in self.events]


def _workflow_input_runthrough_to_compute() -> Workflow:
    """TRIGGER -> INPUT(runThrough=true) -> COMPUTE."""
    return Workflow(
        id="wf-rt",
        name="RunThroughInput",
        nodes=[
            WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="input-rt",
                name="SoftInput",
                node_type=NodeType.INPUT,
                input_binding={"name": "{{$trigger.body.name}}"},
                config={
                    "input_schema": [{"key": "name", "type": "string"}],
                    "runThrough": True,
                },
            ),
            WorkflowNode(
                id="compute-after",
                name="ComputeAfter",
                node_type=NodeType.COMPUTE,
                config={"expression": "1 + 1", "result_key": "tally"},
            ),
        ],
        edges=[
            WorkflowEdge(source_node_id="trigger", target_node_id="input-rt"),
            WorkflowEdge(source_node_id="input-rt", target_node_id="compute-after"),
        ],
    )


@pytest.fixture
def deps():
    file_client = MockFileServiceClient()
    csv_converter = CsvConverter()
    data_store = DataStoreService(file_client, csv_converter)

    resolver = VariableResolver(strategies=[
        FileBackedResolverStrategy(data_store),
        MetadataResolverStrategy(),
        TraversalResolverStrategy(),
    ])

    registry = NodeHandlerRegistry()
    registry.register(NodeType.TRIGGER, TriggerNodeHandler())
    registry.register(NodeType.INPUT, InputNodeHandler(resolver, data_store_service=data_store))
    registry.register(NodeType.COMPUTE, ComputeNodeHandler(resolver))

    task_factory = TaskFactory()
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    workflow_repo = MemoryWorkflowRepository()
    stage_recorder = StageRecorder(MemoryStageHistoryRepository())

    event_publisher = _CapturingEventPublisher()
    # Node outputs are view-only (Phase 3b.1): downstream input resolution
    # (e.g. the INPUT node referencing ``$trigger``) reads through the
    # projector view, so the harness needs the event-log → view chain.
    event_log_repo = MemoryEventLogRepository()
    logging_publisher = EventLogAppendingPublisher(
        inner=event_publisher,
        event_log_repo=event_log_repo,
        logger=_DummyLogger(),
    )
    view_store = CachedProjectorRunContextViewStore(
        event_log_repo,
        workflow_repo=workflow_repo,
        run_repo=run_repo,
    )
    successor_processor = SuccessorProcessor(
        registry, task_factory, data_store_service=data_store,
    )

    engine = OrchestrationEngine(
        handler_registry=registry,
        task_factory=task_factory,
        successor_processor=successor_processor,
        stage_recorder=stage_recorder,
        task_repo=task_repo,
        run_repo=run_repo,
        event_publisher=logging_publisher,
        file_service_client=file_client,
        csv_converter=csv_converter,
        workflow_repo=workflow_repo,
        variable_resolver=resolver,
        data_store_service=data_store,
        run_context_view_store=view_store,
    )

    queue = MemoryOrchestrationQueue()
    engine.set_queue(queue)

    return {
        "queue": queue,
        "view_store": view_store,
        "logger": _DummyLogger(),
        "workflow_repo": workflow_repo,
        "run_repo": run_repo,
        "task_repo": task_repo,
        "stage_recorder": stage_recorder,
        "orchestration_engine": engine,
        "event_publisher": event_publisher,
        "data_store_service": data_store,
    }


async def _drain_queue(deps, *, max_steps: int = 300) -> int:
    """Drive the production ``OrchestrationWorker`` over the engine's queue
    until it quiesces — StartRunUseCase enqueues instead of orchestrating
    inline."""
    worker = OrchestrationWorker(deps["queue"], deps["orchestration_engine"])
    steps = 0
    while steps < max_steps:
        try:
            action = await asyncio.wait_for(deps["queue"].dequeue(), timeout=0.05)
        except asyncio.TimeoutError:
            return steps
        await worker._dispatch(action)
        steps += 1
    raise AssertionError(f"orchestration queue did not drain within {max_steps} steps")


async def _start(deps, input_data: dict) -> str:
    uc = StartRunUseCase(
        logger=deps["logger"],
        workflow_repo=deps["workflow_repo"],
        run_repo=deps["run_repo"],
        task_repo=deps["task_repo"],
        orchestration_engine=deps["orchestration_engine"],
        stage_recorder=deps["stage_recorder"],
        event_publisher=deps["event_publisher"],
        data_store_service=deps["data_store_service"],
        orchestration_queue=None,
    )
    result = await uc.execute(StartRunCommand(workflow_id="wf-rt", input_data=input_data))
    assert (result.error or "") == "", f"start failed: {result.error}"
    await _drain_queue(deps)
    return result.id


@pytest.mark.asyncio
async def test_run_through_input_completes_without_pause(deps):
    """The run reaches COMPLETED without any resume call when INPUT has runThrough."""
    deps["workflow_repo"].save(_workflow_input_runthrough_to_compute())

    run_id = await _start(deps, {"name": "Alice"})
    run = deps["run_repo"].get_by_id(run_id)

    assert run.status == RunStatus.COMPLETED, (
        f"run did not complete synchronously; status={run.status}"
    )


@pytest.mark.asyncio
async def test_run_through_input_still_emits_input_requested(deps):
    """input.requested is broadcast even though the run does not pause."""
    deps["workflow_repo"].save(_workflow_input_runthrough_to_compute())

    await _start(deps, {"name": "Alice"})
    topics = deps["event_publisher"].topics()

    assert "input.requested" in topics, (
        f"expected input.requested event to fire; got {topics}"
    )


@pytest.mark.asyncio
async def test_run_through_input_downstream_can_read_output(deps):
    """Successor nodes can resolve {{$input-rt.output.x}} after auto-completion."""
    deps["workflow_repo"].save(_workflow_input_runthrough_to_compute())

    run_id = await _start(deps, {"name": "Alice"})
    run = deps["run_repo"].get_by_id(run_id)

    # input-rt's output should contain the prefilled values from
    # input_binding. Node outputs are view-only (Phase 3b.1) — read
    # through the projector view.
    view = deps["view_store"].get(run_id)
    output_var = view.variables.get("input-rt")
    assert output_var is not None, "INPUT node output not stored in projector view"


@pytest.mark.asyncio
async def test_run_through_input_task_is_completed_not_pending(deps):
    """The INPUT node's task ends in COMPLETED status, never PENDING."""
    deps["workflow_repo"].save(_workflow_input_runthrough_to_compute())

    run_id = await _start(deps, {"name": "Alice"})
    tasks = deps["task_repo"].list_by_run(run_id)

    input_tasks = [t for t in tasks if t.node_id == "input-rt"]
    assert input_tasks, "no task recorded for the INPUT node"
    for task in input_tasks:
        assert task.status == TaskStatus.COMPLETED, (
            f"INPUT task status was {task.status}, expected COMPLETED"
        )
