"""SA-846 open item: a cycle/late second arrival into a WORKFLOW node
must re-trigger the child workflow.

Repro for "revisiting a WORKFLOW node does not trigger anything": the
deterministic child-run id is keyed by (parent_run, node, generation)
only — a revisit recomputes the SAME child run id, and the second
``run_repo.asave(child_run)`` collides with the existing row.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.events import EventTopic
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.node_handlers.workflow_handler import WorkflowNodeHandler
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    ConditionEvaluator,
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer2_application.interfaces.orchestration_queue_interface import (
    ExecuteNodeAction,
    LoopIterationAction,
    StartRunAction,
)
from app.layer2_application.orchestration_queue.memory_queue import MemoryOrchestrationQueue
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository


class _CapturingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def publish(self, topic, event):
        self.events.append((topic, event))

    def publish_with_headers(self, topic, event, *, headers=None, key=None):
        self.events.append((topic, event))

    def publish_many(self, items):
        for entry in items:
            if isinstance(entry, tuple) and len(entry) == 2:
                self.events.append(entry)

    def events_of(self, topic: str) -> list[object]:
        return [e for t, e in self.events if t == topic]


def _build_harness(workflow: Workflow, child_workflow: Workflow):
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

    registry = NodeHandlerRegistry()
    registry.register(NodeType.TRIGGER, TriggerNodeHandler())
    registry.register(NodeType.COMPUTE, ComputeNodeHandler(resolver))
    registry.register(NodeType.WORKFLOW, WorkflowNodeHandler(resolver))

    task_factory = TaskFactory()
    successor_processor = SuccessorProcessor(
        registry, task_factory, data_store_service=data_store,
    )

    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    workflow_repo = MemoryWorkflowRepository()
    workflow_repo.save(workflow)
    workflow_repo.save(child_workflow)

    stage_recorder = StageRecorder(MagicMock())
    stage_recorder.record = MagicMock()

    publisher = _CapturingPublisher()

    engine = OrchestrationEngine(
        handler_registry=registry,
        task_factory=task_factory,
        successor_processor=successor_processor,
        stage_recorder=stage_recorder,
        task_repo=task_repo,
        run_repo=run_repo,
        event_publisher=publisher,
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
        "publisher": publisher,
        "run_repo": run_repo,
        "task_repo": task_repo,
    }


async def _drive_queue(engine, queue, *, max_steps: int = 200) -> int:
    steps = 0
    while steps < max_steps:
        try:
            action = await asyncio.wait_for(queue.dequeue(), timeout=0.05)
        except asyncio.TimeoutError:
            return steps
        steps += 1
        if isinstance(action, StartRunAction):
            await engine.orchestrate_start(action.run_id)
        elif isinstance(action, ExecuteNodeAction):
            await engine.orchestrate_execute_node(action.run_id, action.task_id)
        elif isinstance(action, LoopIterationAction):
            await engine.orchestrate_loop_iteration(
                action.run_id, action.task_id,
                action.loop_node_id, action.iteration_index,
            )
        else:
            raise AssertionError(f"unexpected action type: {type(action).__name__}")
    raise AssertionError(f"queue did not drain within {max_steps} steps")


@pytest.mark.asyncio
async def test_workflow_node_reruns_child_on_late_second_arrival():
    """A second taken edge into a completed WORKFLOW node must spawn a
    fresh child run (same revisit semantics as every other node type).

    SA-846 Mode A (fixed): the arrival lands during the child-run window
    (the tracking task stays RUNNING for the whole child duration), so the
    dispatch gate records a pending revisit instead of dropping the edge;
    ``check_run_completion`` drains it once the first child completes and
    the WORKFLOW node re-runs with a fresh visit-keyed child run.

    Graph (uneven path lengths so ``w`` completes its first child run
    before the second edge arrives)::

        trigger ── a ──────────────► w (WORKFLOW → child-wf)
        trigger ── b ── c ─────────► w
    """
    expr = {"expression": "1 + 1"}
    child_workflow = Workflow(
        id="child-wf",
        name="child",
        version="1",
        nodes=[WorkflowNode(id="cw-compute", node_type=NodeType.COMPUTE, config=expr)],
        edges=[],
    )
    workflow = Workflow(
        id="wf-w-revisit",
        name="w-revisit",
        version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="a", node_type=NodeType.COMPUTE, config=expr),
            WorkflowNode(id="b", node_type=NodeType.COMPUTE, config=expr),
            WorkflowNode(id="c", node_type=NodeType.COMPUTE, config=expr),
            WorkflowNode(
                id="w", node_type=NodeType.WORKFLOW,
                config={"workflow_id": "child-wf"},
            ),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="a"),
            WorkflowEdge(id="e2", source_node_id="trigger", target_node_id="b"),
            WorkflowEdge(id="e3", source_node_id="a", target_node_id="w"),
            WorkflowEdge(id="e4", source_node_id="b", target_node_id="c"),
            WorkflowEdge(id="e5", source_node_id="c", target_node_id="w"),
        ],
    )
    harness = _build_harness(workflow, child_workflow)

    from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
    from app.layer1_domain.entities.workflow_run import GraphTopology

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-w-revisit", workflow_id=workflow.id,
        status=RunStatus.RUNNING,
        context=RunContext(topology=topology, variables={"input": {}}),
    )
    harness["run_repo"].save(run)
    await harness["engine"].orchestrate_start("run-w-revisit")
    await _drive_queue(harness["engine"], harness["queue"])
    run = harness["run_repo"].get_by_id("run-w-revisit")

    child_runs = [
        r for r in harness["run_repo"]._store.values()
        if r.parent_run_id == "run-w-revisit"
    ]
    w_tasks = sorted(
        (t for t in harness["task_repo"].list_by_run("run-w-revisit")
         if t.node_id == "w"),
        key=lambda t: t.id,
    )
    child_started = harness["publisher"].events_of(EventTopic.CHILD_RUN_STARTED)

    print(f"\nrun.status={run.status} error={run.error!r}")
    print(f"w tasks: {[(t.id, t.status) for t in w_tasks]}")
    print(f"child runs: {[(r.id, r.status) for r in child_runs]}")
    print(f"ChildRunStarted events: {len(child_started)}")
    print(f"node_visits={run.context.node_visits}")

    assert run.status == RunStatus.COMPLETED, f"run ended {run.status}: {run.error}"
    assert len(child_started) == 2, (
        f"expected 2 child runs (one per arrival into w), got {len(child_started)}"
    )
    assert len({e.child_run_id for e in child_started}) == 2, (
        "revisit reused the SAME child run id — deterministic id ignores visit"
    )


@pytest.mark.asyncio
async def test_workflow_node_reruns_child_after_first_child_completed():
    """Second arrival lands AFTER the first child run completed (the true
    cycle-re-entry shape): the revisit must spawn a FRESH child run.

    Regression (SA-846 Mode B): ``deterministic_child_run_id`` ignored the
    visit counter, so the revisit recomputed visit 0's child run id and the
    fresh-entity child-run save hit an ``OptimisticConcurrencyError``,
    failing the parent run.

    Graph — second path long enough that the child's entry node executes
    (and completes the child) before the second edge into ``w`` fires::

        trigger ── a ──────────────────► w (WORKFLOW → child-wf)
        trigger ── b ── c ── d ────────► w
    """
    expr = {"expression": "1 + 1"}
    child_workflow = Workflow(
        id="child-wf",
        name="child",
        version="1",
        nodes=[WorkflowNode(id="cw-compute", node_type=NodeType.COMPUTE, config=expr)],
        edges=[],
    )
    workflow = Workflow(
        id="wf-w-revisit2",
        name="w-revisit2",
        version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="a", node_type=NodeType.COMPUTE, config=expr),
            WorkflowNode(id="b", node_type=NodeType.COMPUTE, config=expr),
            WorkflowNode(id="c", node_type=NodeType.COMPUTE, config=expr),
            WorkflowNode(id="d", node_type=NodeType.COMPUTE, config=expr),
            WorkflowNode(
                id="w", node_type=NodeType.WORKFLOW,
                config={"workflow_id": "child-wf"},
            ),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="a"),
            WorkflowEdge(id="e2", source_node_id="trigger", target_node_id="b"),
            WorkflowEdge(id="e3", source_node_id="a", target_node_id="w"),
            WorkflowEdge(id="e4", source_node_id="b", target_node_id="c"),
            WorkflowEdge(id="e5", source_node_id="c", target_node_id="d"),
            WorkflowEdge(id="e6", source_node_id="d", target_node_id="w"),
        ],
    )
    harness = _build_harness(workflow, child_workflow)

    from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
    from app.layer1_domain.entities.workflow_run import GraphTopology

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-w-revisit2", workflow_id=workflow.id,
        status=RunStatus.RUNNING,
        context=RunContext(topology=topology, variables={"input": {}}),
    )
    harness["run_repo"].save(run)
    await harness["engine"].orchestrate_start("run-w-revisit2")
    await _drive_queue(harness["engine"], harness["queue"])
    run = harness["run_repo"].get_by_id("run-w-revisit2")

    child_runs = [
        r for r in harness["run_repo"]._store.values()
        if r.parent_run_id == "run-w-revisit2"
    ]
    w_tasks = sorted(
        (t for t in harness["task_repo"].list_by_run("run-w-revisit2")
         if t.node_id == "w"),
        key=lambda t: t.id,
    )
    child_started = harness["publisher"].events_of(EventTopic.CHILD_RUN_STARTED)

    print(f"\nrun.status={run.status} error={run.error!r}")
    print(f"w tasks: {[(t.id, t.status) for t in w_tasks]}")
    print(f"child runs: {[(r.id, r.status) for r in child_runs]}")
    print(f"ChildRunStarted events: {len(child_started)}")
    print(f"node_visits={run.context.node_visits}")

    assert run.status == RunStatus.COMPLETED, f"run ended {run.status}: {run.error}"
    assert len(child_started) == 2, (
        f"expected 2 child runs (one per arrival into w), got {len(child_started)}"
    )
    assert len({e.child_run_id for e in child_started}) == 2, (
        "revisit reused the SAME child run id — deterministic id ignores visit"
    )
