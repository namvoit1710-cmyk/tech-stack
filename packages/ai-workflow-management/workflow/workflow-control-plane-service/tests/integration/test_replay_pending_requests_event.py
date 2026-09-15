"""Integration test: ``/replay-pending-requests`` must re-fire ``*.requested``
events for PENDING interactive tasks on descendant sub-workflow runs, not
just the root run.

Wires up the real ``OrchestrationEngine`` + real ``InputNodeHandler`` + a
capturing event publisher so the test pins the on-the-wire contract end-to-end
rather than mocking the engine. Catches:

- The use case forgetting to call ``drain_events()`` after asking the engine
  to re-request (events would buffer in the EventCollector contextvar and
  silently disappear when the request handler returns).
- A future refactor that swaps the engine's buffered emit for a direct
  publish without keeping the wire contract intact.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext
from app.layer1_domain.entities.task import Task
from app.layer1_domain.events import InputRequested
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus

from app.layer2_application.data.csv_converter import CsvConverter
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.input_handler import InputNodeHandler
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)

from app.layer2_application.features.replay_pending_requests.use_cases.replay_pending_requests_use_case import (
    ReplayPendingRequestsCommand,
    ReplayPendingRequestsUseCase,
)

from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import MemoryStageHistoryRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository


class _DummyLogger:
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


class _CapturingEventPublisher:
    """Records every (topic, event) pair in publish-order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def publish(self, topic, event) -> None:
        self.events.append((topic, event))

    def topics(self) -> list[str]:
        return [t for t, _ in self.events]


def _child_workflow_with_input_entry() -> Workflow:
    return Workflow(
        id="child-wf",
        name="ChildWithInputEntry",
        nodes=[
            WorkflowNode(
                id="child-input",
                name="CollectInput",
                node_type=NodeType.INPUT,
                config={
                    "instruction": "Please provide input",
                    "input_schema": [
                        {"key": "name", "type": "string", "label": "Name"},
                    ],
                },
            ),
        ],
        edges=[],
    )


def _parent_workflow_calls_child() -> Workflow:
    return Workflow(
        id="parent-wf",
        name="Parent",
        nodes=[
            WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="wf-node",
                name="CallChild",
                node_type=NodeType.WORKFLOW,
                config={"workflow_id": "child-wf", "input_mapping": {}, "on_failure": "fail"},
            ),
        ],
        edges=[
            WorkflowEdge(source_node_id="trigger", target_node_id="wf-node"),
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
    registry.register(NodeType.INPUT, InputNodeHandler(resolver, data_store_service=data_store))

    task_factory = TaskFactory()
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    workflow_repo = MemoryWorkflowRepository()
    stage_history_repo = MemoryStageHistoryRepository()
    stage_recorder = StageRecorder(stage_history_repo)

    event_publisher = _CapturingEventPublisher()
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
        event_publisher=event_publisher,
        file_service_client=file_client,
        csv_converter=csv_converter,
        workflow_repo=workflow_repo,
        variable_resolver=resolver,
        data_store_service=data_store,
    )

    return {
        "logger": _DummyLogger(),
        "workflow_repo": workflow_repo,
        "run_repo": run_repo,
        "task_repo": task_repo,
        "stage_recorder": stage_recorder,
        "orchestration_engine": engine,
        "event_publisher": event_publisher,
    }


def _seed_parent_with_pending_child_input(deps) -> tuple[str, str, str]:
    """Set up: parent run holds a child sub-workflow run that's paused on
    an INPUT node with a PENDING task. Returns (parent_run_id, child_run_id,
    old_pending_task_id).
    """
    parent_wf = _parent_workflow_calls_child()
    child_wf = _child_workflow_with_input_entry()
    deps["workflow_repo"].save(parent_wf)
    deps["workflow_repo"].save(child_wf)

    child_dag = GraphTopology.from_workflow(child_wf)
    parent_dag = GraphTopology.from_workflow(parent_wf)

    child_run = WorkflowRun(
        id="child-run",
        workflow_id="child-wf",
        status=RunStatus.PENDING,
        parent_run_id="parent-run",
        parent_node_id="wf-node",
        depth_level=1,
        root_run_id="parent-run",
        context=RunContext(topology=child_dag),
    )
    parent_run = WorkflowRun(
        id="parent-run",
        workflow_id="parent-wf",
        status=RunStatus.RUNNING,
        depth_level=0,
        root_run_id="parent-run",
        context=RunContext(
            child_runs={"wf-node": "child-run"},
            topology=parent_dag,
        ),
    )
    deps["run_repo"].save(parent_run)
    deps["run_repo"].save(child_run)

    old_task = Task(
        id="t-old-child",
        run_id="child-run",
        node_id="child-input",
        status=TaskStatus.PENDING,
        output_metadata={
            "node_type": "INPUT",
            "input_schema": [{"key": "name", "type": "string", "label": "Name"}],
            "instruction": "Please provide input",
        },
    )
    deps["task_repo"].save(old_task)

    return parent_run.id, child_run.id, old_task.id


@pytest.mark.asyncio
async def test_replay_publishes_input_requested_for_child_run(deps):
    """The headline contract: calling /replay-pending-requests on the parent
    run must result in an InputRequested event reaching the wire for the
    *child* run's INPUT node — not just the engine being asked.
    """
    parent_run_id, child_run_id, _old_task_id = _seed_parent_with_pending_child_input(deps)

    uc = ReplayPendingRequestsUseCase(
        logger=deps["logger"],
        run_repo=deps["run_repo"],
        task_repo=deps["task_repo"],
        workflow_repo=deps["workflow_repo"],
        stage_recorder=deps["stage_recorder"],
        orchestration_engine=deps["orchestration_engine"],
    )

    result = await uc.execute(ReplayPendingRequestsCommand(run_id=parent_run_id))

    assert result.replayed is True, f"Replay should have produced entries; got: {result}"
    assert any(e["run_id"] == child_run_id for e in result.entries), (
        f"No replay entry for child run; entries={result.entries}"
    )

    child_input_events = [
        (topic, event) for topic, event in deps["event_publisher"].events
        if isinstance(event, InputRequested) and event.run_id == child_run_id
    ]
    assert child_input_events, (
        "Expected an InputRequested event for the child run to reach the "
        "event publisher after replay. Got topics: "
        f"{deps['event_publisher'].topics()}. "
        "If the publisher is empty, the use case is likely buffering "
        "events into the EventCollector and never draining them — "
        "ReplayPendingRequestsUseCase.execute must call "
        "`await self._engine.drain_events()` before returning, like every "
        "other comparable use case (start_run, resume_run, pause_run, …)."
    )

    _topic, child_event = child_input_events[0]
    assert child_event.node_id == "child-input"
    assert child_event.workflow_id == "child-wf"
    assert child_event.parent_run_id == parent_run_id, (
        "InputRequested.parent_run_id should carry the parent run id so the "
        "frontend can correlate the re-request with the originating run."
    )


@pytest.mark.asyncio
async def test_replay_emits_fresh_task_id_on_child_event(deps):
    """The re-fired event must point at the *new* PENDING task created by
    the replay, not the cancelled one — that's the whole point of the replay.
    """
    parent_run_id, child_run_id, old_task_id = _seed_parent_with_pending_child_input(deps)

    uc = ReplayPendingRequestsUseCase(
        logger=deps["logger"],
        run_repo=deps["run_repo"],
        task_repo=deps["task_repo"],
        workflow_repo=deps["workflow_repo"],
        stage_recorder=deps["stage_recorder"],
        orchestration_engine=deps["orchestration_engine"],
    )

    result = await uc.execute(ReplayPendingRequestsCommand(run_id=parent_run_id))
    assert result.replayed is True

    new_task_id = next(
        e["new_task_id"] for e in result.entries if e["run_id"] == child_run_id
    )

    child_input_events = [
        event for _t, event in deps["event_publisher"].events
        if isinstance(event, InputRequested) and event.run_id == child_run_id
    ]
    assert child_input_events, (
        "No InputRequested event reached the publisher for the child run."
    )
    fresh_event = child_input_events[0]
    assert fresh_event.task_id == new_task_id, (
        f"Re-fired event carried task_id={fresh_event.task_id!r}, expected "
        f"the freshly-created task id {new_task_id!r}; old task was {old_task_id!r}."
    )
    assert fresh_event.task_id != old_task_id
