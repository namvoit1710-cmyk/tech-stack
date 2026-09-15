"""Parallel fan-out dispatch: 1 CONDITION → 2 TASK successors.

User scenario: a CONDITION node whose ``true_branch`` has TWO outgoing
edges, each leading to a TASK node (e.g. wait-worker with 5 s / 10 s
durations). Both successor tasks must be **dispatched in parallel** —
neither waiting for the other's worker callback. The actual wait runs
concurrently in the worker; this test pins the contract at the engine
level so the architecture supports the fan-out.

What's proven:

1. After CONDITION fires, both leaf tasks reach ``TaskStatus.RUNNING``
   without any worker callback (no real worker in the test). If the
   engine were serialising (waiting for one to complete before
   dispatching the next) the second task would never leave ``READY``.
2. The per-run ``asyncio.Lock`` in
   :meth:`OrchestrationEngine.orchestrate_execute_node` releases
   between successor dispatches — the two ``node.dispatched`` events
   fire within milliseconds.
3. Neither leaf emits ``node.completed`` between the two leaf
   ``node.dispatched`` events — i.e. the engine doesn't synchronously
   run a leaf to completion before moving to its sibling.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.events import EventTopic
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.condition_handler import ConditionNodeHandler
from app.layer2_application.node_handlers.task_handler import TaskNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.resolve.variable_resolver import (
    ConditionEvaluator,
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.interfaces.orchestration_queue_interface import (
    ExecuteNodeAction,
    StartRunAction,
)
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer2_application.orchestration_queue.memory_queue import MemoryOrchestrationQueue
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository


class _StampedCapturingPublisher:
    """Captures events plus the monotonic_ns at which they were published."""

    def __init__(self) -> None:
        # (topic, event, timestamp_ns)
        self.events: list[tuple[str, object, int]] = []

    def publish(self, topic, event):
        self.events.append((topic, event, time.monotonic_ns()))

    def publish_with_headers(self, topic, event, *, headers=None, key=None):
        self.events.append((topic, event, time.monotonic_ns()))

    def publish_many(self, items):
        for entry in items:
            if isinstance(entry, tuple) and len(entry) == 2:
                self.events.append((entry[0], entry[1], time.monotonic_ns()))

    def events_of(self, topic: str) -> list[tuple[object, int]]:
        return [(e, ts) for t, e, ts in self.events if t == topic]


def _build_harness(workflow: Workflow):
    file_client = MockFileServiceClient()
    csv_converter = CsvConverter()
    data_store = DataStoreService(file_client, csv_converter)

    resolver = VariableResolver(strategies=[
        FileBackedResolverStrategy(data_store, file_client=file_client),
        MetadataResolverStrategy(),
        TraversalResolverStrategy(),
    ])
    cond_eval = ConditionEvaluator(resolver)

    registry = NodeHandlerRegistry()
    registry.register(NodeType.TRIGGER, TriggerNodeHandler())
    registry.register(NodeType.CONDITION, ConditionNodeHandler(cond_eval))
    registry.register(NodeType.TASK, TaskNodeHandler(resolver))

    task_factory = TaskFactory()
    successor_processor = SuccessorProcessor(
        registry, task_factory, data_store_service=data_store,
    )

    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    workflow_repo = MemoryWorkflowRepository()
    workflow_repo.save(workflow)

    stage_recorder = StageRecorder(MagicMock())
    stage_recorder.record = MagicMock()

    publisher = _StampedCapturingPublisher()
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
        "workflow_repo": workflow_repo,
    }


async def _drive_until_idle(engine, queue, *, max_steps: int = 50, idle_timeout: float = 0.05) -> int:
    """Single-coroutine drain; returns when the queue is idle."""
    steps = 0
    while steps < max_steps:
        try:
            action = await asyncio.wait_for(queue.dequeue(), timeout=idle_timeout)
        except asyncio.TimeoutError:
            return steps
        steps += 1
        if isinstance(action, StartRunAction):
            await engine.orchestrate_start(action.run_id)
        elif isinstance(action, ExecuteNodeAction):
            await engine.orchestrate_execute_node(action.run_id, action.task_id)
        else:
            raise AssertionError(f"unexpected action type: {type(action).__name__}")
    raise AssertionError(f"queue did not drain within {max_steps} steps")


@pytest.mark.asyncio
async def test_condition_true_dispatches_two_task_successors_in_parallel():
    workflow = Workflow(
        id="wf-parallel-fanout",
        name="parallel-fanout",
        version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="cond", node_type=NodeType.CONDITION,
                config={"expression": "1 == 1"},  # always true
            ),
            WorkflowNode(
                id="task_a", node_type=NodeType.TASK,
                worker_type="wait",
                config={"duration_seconds": 5},
            ),
            WorkflowNode(
                id="task_b", node_type=NodeType.TASK,
                worker_type="wait",
                config={"duration_seconds": 10},
            ),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="cond"),
            WorkflowEdge(
                id="e_a", source_node_id="cond", target_node_id="task_a",
                source_port_id="true_branch",
            ),
            WorkflowEdge(
                id="e_b", source_node_id="cond", target_node_id="task_b",
                source_port_id="true_branch",
            ),
        ],
    )
    harness = _build_harness(workflow)

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-fanout", workflow_id=workflow.id,
        status=RunStatus.RUNNING,
        context=RunContext(topology=topology, variables={"input": {}}),
    )
    harness["run_repo"].save(run)

    await harness["engine"].orchestrate_start("run-fanout")
    await _drive_until_idle(harness["engine"], harness["queue"])

    # ── 1. Both leaf tasks reached RUNNING (dispatched, awaiting worker) ──
    all_tasks = harness["task_repo"].list_by_run("run-fanout")
    task_a = [t for t in all_tasks if t.node_id == "task_a"]
    task_b = [t for t in all_tasks if t.node_id == "task_b"]
    assert len(task_a) == 1, f"expected exactly one task for task_a, got {len(task_a)}"
    assert len(task_b) == 1, f"expected exactly one task for task_b, got {len(task_b)}"
    assert task_a[0].status == TaskStatus.RUNNING, (
        f"task_a must be RUNNING after fan-out dispatch (worker callback not yet "
        f"landed); got {task_a[0].status}. If task_a is RUNNING but task_b is "
        f"READY/PENDING, the engine is serialising successor dispatches."
    )
    assert task_b[0].status == TaskStatus.RUNNING, (
        f"task_b must be RUNNING after fan-out dispatch (worker callback not yet "
        f"landed); got {task_b[0].status}. This is the smoking gun for the "
        f"'1 node at a time' symptom — the second sibling was never dispatched."
    )

    # ── 2. Both NodeDispatched events emitted, one per leaf ──────────────
    dispatched_events = harness["publisher"].events_of(EventTopic.NODE_DISPATCHED)
    leaf_dispatches = [(e, ts) for e, ts in dispatched_events if e.node_id in ("task_a", "task_b")]
    leaf_node_ids = sorted(e.node_id for e, _ in leaf_dispatches)
    assert leaf_node_ids == ["task_a", "task_b"], (
        f"both leaves must emit node.dispatched exactly once; got {leaf_node_ids}"
    )

    # ── 3. No leaf node.completed between the two leaf dispatches ────────
    earliest = min(ts for _, ts in leaf_dispatches)
    latest = max(ts for _, ts in leaf_dispatches)
    leaf_completions_in_window = [
        e.node_id
        for e, ts in harness["publisher"].events_of(EventTopic.NODE_COMPLETED)
        if e.node_id in ("task_a", "task_b") and earliest <= ts <= latest
    ]
    assert leaf_completions_in_window == [], (
        f"no leaf may complete between the two leaf dispatches — that would "
        f"prove the engine ran one leaf to completion before dispatching its "
        f"sibling. Offending completions: {leaf_completions_in_window}"
    )

    # ── 4. Dispatch handoff is fast (sanity bound for the per-run lock) ──
    delta_ms = (latest - earliest) / 1_000_000
    assert delta_ms < 200, (
        f"the two leaf dispatches should happen within milliseconds; "
        f"observed {delta_ms:.1f} ms. If this is seconds, investigate the "
        f"per-run asyncio.Lock window in orchestrate_execute_node — most "
        f"likely a blocking I/O call (file upload, HANA save, or input "
        f"resolution) is widening the critical section."
    )

    # ── 5. Run remains RUNNING (no worker callbacks landed yet) ──────────
    final_run = harness["run_repo"].get_by_id("run-fanout")
    assert final_run.status == RunStatus.RUNNING, (
        f"run must remain RUNNING while leaves are dispatched but no worker "
        f"callback has landed; got {final_run.status}"
    )
