"""Integration test for the parent->child workflow event ordering bug.

Regression: when a parent's WORKFLOW node dispatches a child whose first
node is an INPUT (PENDING) type, the wire used to receive
``input.requested`` BEFORE the ``node.dispatched`` and ``child_run.started``
events that the frontend relies on for setup.

Root cause was duplicate ``publish_event`` / ``publish_events`` definitions
in ``OrchestrationEngine`` — the second pair fired immediately, bypassing
the EventCollector that exists precisely to enforce the priority + insertion
ordering this test pins down.

Test driver: ``StartRunUseCase`` exercises the production path
(orchestrate_start -> handle_entry_nodes -> dispatch_or_handle_task ->
_start_child_workflow -> handle_entry_nodes for the child's INPUT entry).
We capture every (topic, event) reaching the publisher and assert the
relative order.
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
from app.layer2_application.node_handlers.task_handler import TaskNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.node_handlers.workflow_handler import WorkflowNodeHandler
from app.layer2_application.node_handlers.input_handler import InputNodeHandler

from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import MemoryStageHistoryRepository

from app.layer2_application.orchestration_queue.memory_queue import (
    MemoryOrchestrationQueue,
)
from app.layer4_frameworks.orchestration_queue.worker import OrchestrationWorker
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunUseCase, StartRunCommand,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _DummyLogger:
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


class _CapturingEventPublisher:
    """Records every (topic, event) tuple in publish-order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def publish(self, topic, event) -> None:
        self.events.append((topic, event))

    def topics(self) -> list[str]:
        return [t for t, _ in self.events]


# ---------------------------------------------------------------------------
# Workflow fixtures
# ---------------------------------------------------------------------------

def _parent_workflow_calls_child() -> Workflow:
    """TRIGGER -> WORKFLOW(child)  (WORKFLOW is the parent's leaf)."""
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


def _child_workflow_with_input_entry() -> Workflow:
    """Child whose only node is INPUT (entry + leaf, PENDING type)."""
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


def _child_workflow_with_inline_node() -> Workflow:
    """Child whose only node is TRIGGER (inline BUILTIN — completes immediately).

    Used to verify event ordering for a nested workflow whose sub-workflow
    completes inline (no PENDING wait, no external worker).
    """
    return Workflow(
        id="child-wf",
        name="ChildWithInlineNode",
        nodes=[
            WorkflowNode(
                id="child-trigger",
                name="ChildInlineTrigger",
                node_type=NodeType.TRIGGER,
            ),
        ],
        edges=[],
    )


# ---------------------------------------------------------------------------
# DI fixture
# ---------------------------------------------------------------------------

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
    registry.register(NodeType.TASK, TaskNodeHandler(resolver))
    registry.register(NodeType.WORKFLOW, WorkflowNodeHandler(resolver))
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

    queue = MemoryOrchestrationQueue()
    engine.set_queue(queue)

    return {
        "logger": _DummyLogger(),
        "queue": queue,
        "workflow_repo": workflow_repo,
        "run_repo": run_repo,
        "task_repo": task_repo,
        "stage_recorder": stage_recorder,
        "orchestration_engine": engine,
        "event_publisher": event_publisher,
        "data_store_service": data_store,
        "file_client": file_client,
    }


async def _drain_queue(deps, *, max_steps: int = 300) -> int:
    """Drive the production ``OrchestrationWorker`` over the engine's queue
    until it quiesces — StartRunUseCase enqueues a StartRunAction instead of
    orchestrating inline, so every start must be followed by a drain."""
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


async def _start_parent_run(deps) -> str:
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
    result = await uc.execute(StartRunCommand(workflow_id="parent-wf", input_data={}))
    assert (result.error or "") == "", f"Parent start failed: {result.error}"
    await _drain_queue(deps)
    return result.id


def _first_index(topics: list[str], predicate) -> int:
    for i, t in enumerate(topics):
        if predicate(t):
            return i
    raise AssertionError(f"No topic matched predicate in {topics}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestChildWorkflowInputEntryEventOrder:
    """Pin down the on-the-wire event order for parent -> child(INPUT entry).

    Current (uniform-execution) wire order:
        run.status(started), …, node.dispatched (trigger),
        node.completed (trigger), node.input (wf-node),
        child_run.started, …, input.requested, run.status(pending)

    The WORKFLOW node's ``node.dispatched`` is emitted by the child_run
    mode runner AFTER the child bootstrap, so the parent-side handoff
    boundary that precedes ``child_run.started`` is ``node.input``.
    """

    @pytest.mark.asyncio
    async def test_child_input_does_not_fire_before_parent_task_dispatched(self, deps):
        """The user-reported symptom: ``input.requested`` must not arrive
        before the parent ``node.dispatched`` events.

        This is the headline regression test for the duplicate-method bug.
        """
        deps["workflow_repo"].save(_parent_workflow_calls_child())
        deps["workflow_repo"].save(_child_workflow_with_input_entry())

        parent_run_id = await _start_parent_run(deps)
        topics = deps["event_publisher"].topics()

        # The InputRequested event reaches the wire as topic "input.requested"
        # (class-name lowercased by publish_events). The realtime layer maps
        # it to "input.requested" before reaching the frontend.
        assert "input.requested" in topics, (
            f"Expected an input.requested event to be published; got {topics}"
        )

        first_task_dispatched = _first_index(topics, lambda t: t == "node.dispatched")
        first_input_requested = _first_index(topics, lambda t: t == "input.requested")

        assert first_task_dispatched < first_input_requested, (
            f"REGRESSION: input.requested fired before node.dispatched. "
            f"Order was: {topics}. The duplicate publish_event/publish_events "
            f"definitions are likely back — they bypass the EventCollector "
            f"and emit immediately, scrambling the wire order."
        )

    @pytest.mark.asyncio
    async def test_child_run_started_precedes_child_input_requested(self, deps):
        """The frontend subscribes to ``run:{child_run_id}`` upon seeing
        ``child_run.started`` and expects ``input.requested`` afterwards.
        If they arrive in the opposite order the subscription misses the
        input request.
        """
        deps["workflow_repo"].save(_parent_workflow_calls_child())
        deps["workflow_repo"].save(_child_workflow_with_input_entry())

        await _start_parent_run(deps)
        topics = deps["event_publisher"].topics()

        idx_child_started = _first_index(topics, lambda t: t == "child_run.started")
        idx_input_requested = _first_index(topics, lambda t: t == "input.requested")

        assert idx_child_started < idx_input_requested, (
            f"child_run.started must precede input.requested; got order: {topics}"
        )

    @pytest.mark.asyncio
    async def test_parent_workflow_handoff_precedes_child_run_started(self, deps):
        """The parent WORKFLOW node's handoff boundary — ``node.input`` —
        must arrive before ``child_run.started``. (``node.dispatched`` for
        the WORKFLOW node is emitted by the child_run mode runner AFTER
        the child bootstrap under uniform execution, so it is no longer
        the boundary marker.)
        """
        deps["workflow_repo"].save(_parent_workflow_calls_child())
        deps["workflow_repo"].save(_child_workflow_with_input_entry())

        parent_run_id = await _start_parent_run(deps)

        wf_input_idx = None
        child_started_idx = None
        for i, (topic, event) in enumerate(deps["event_publisher"].events):
            node_id = getattr(event, "node_id", None) or (
                event.get("node_id") if isinstance(event, dict) else None
            )
            if topic == "node.input" and node_id == "wf-node" and wf_input_idx is None:
                wf_input_idx = i
            if topic == "child_run.started" and child_started_idx is None:
                child_started_idx = i

        assert wf_input_idx is not None, (
            "No node.input event for the WORKFLOW node was captured"
        )
        assert child_started_idx is not None, "No child_run.started event captured"
        assert wf_input_idx < child_started_idx, (
            f"WORKFLOW node.input (idx={wf_input_idx}) must precede "
            f"child_run.started (idx={child_started_idx}). "
            f"Topics: {deps['event_publisher'].topics()}"
        )

    @pytest.mark.asyncio
    async def test_child_input_task_is_pending_after_start(self, deps):
        """Sanity check that we actually drove the orchestrator into the
        pending-input path — otherwise the ordering assertions above
        could pass vacuously.
        """
        deps["workflow_repo"].save(_parent_workflow_calls_child())
        deps["workflow_repo"].save(_child_workflow_with_input_entry())

        parent_run_id = await _start_parent_run(deps)
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        child_run_id = parent_run.context.child_runs.get("wf-node")
        assert child_run_id, (
            f"Expected a child run for wf-node; got: {parent_run.context.child_runs}"
        )

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert child_run.status == RunStatus.PENDING, (
            f"Child run should be PENDING (waiting on INPUT); got {child_run.status}"
        )

        input_tasks = [
            t for t in deps["task_repo"].list_by_run(child_run_id)
            if t.node_id == "child-input"
        ]
        assert len(input_tasks) == 1
        assert input_tasks[0].status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_per_node_dispatch_precedes_completion(self, deps):
        """The priority-lane drain (IMMEDIATE before NORMAL) was retired —
        events now reach the wire in causal order. Pin the per-node
        contract instead: every node's ``node.dispatched`` precedes its
        own ``node.completed``, and the run's ``run.status(started)`` is
        the first event on the wire.
        """
        deps["workflow_repo"].save(_parent_workflow_calls_child())
        deps["workflow_repo"].save(_child_workflow_with_input_entry())

        await _start_parent_run(deps)
        events = deps["event_publisher"].events

        assert str(events[0][0]) == "run.status" and getattr(
            events[0][1], "status", ""
        ) == "started", f"first wire event must be run.status(started); got {events[0]}"

        first_dispatched: dict[str, int] = {}
        first_completed: dict[str, int] = {}
        for i, (topic, event) in enumerate(events):
            node_id = getattr(event, "node_id", None)
            if not node_id:
                continue
            if topic == "node.dispatched":
                first_dispatched.setdefault(node_id, i)
            elif topic == "node.completed":
                first_completed.setdefault(node_id, i)

        for node_id, completed_idx in first_completed.items():
            assert node_id in first_dispatched, (
                f"{node_id} completed without a dispatched event; "
                f"topics={deps['event_publisher'].topics()}"
            )
            assert first_dispatched[node_id] < completed_idx, (
                f"{node_id}: node.dispatched (idx={first_dispatched[node_id]}) "
                f"must precede node.completed (idx={completed_idx}); "
                f"topics={deps['event_publisher'].topics()}"
            )


# ---------------------------------------------------------------------------
# Sub-workflow with an inline (non-pending) entry node
# ---------------------------------------------------------------------------


class TestSubWorkflowWithInlineNodeEventOrder:
    """Parent -> WORKFLOW(child) -> child has only an INLINE node (TRIGGER).

    Unlike the INPUT-entry case (which pauses the child run), an inline
    node runs to completion synchronously. The expected end-to-end wire
    order on a full successful run:

      node.dispatched (parent trigger)   IMMEDIATE
      node.dispatched (parent WORKFLOW)  IMMEDIATE
      child_run.started                  IMMEDIATE
      node.dispatched (child trigger)    IMMEDIATE
      node.completed (parent trigger)    NORMAL
      node.completed (child trigger)     NORMAL
      node.completed (parent WORKFLOW)   NORMAL
      child_run.completed                NORMAL
      run.completed (child)              TERMINAL
      run.completed (parent)             TERMINAL

    Causal constraints we pin down:
    - child_run.started precedes every event scoped to the child run.
    - The child's run.completed precedes the parent's run.completed.
    - Every child node.dispatched arrives before its node.completed.
    - The parent's WORKFLOW node.dispatched precedes the child run starting.
    """

    def _setup(self, deps):
        deps["workflow_repo"].save(_parent_workflow_calls_child())
        deps["workflow_repo"].save(_child_workflow_with_inline_node())

    @pytest.mark.asyncio
    async def test_child_run_completes_inline(self, deps):
        """Sanity: confirm the child actually completes (otherwise the
        ordering assertions below could pass vacuously on a stalled run).
        """
        self._setup(deps)
        parent_run_id = await _start_parent_run(deps)

        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        child_run_id = parent_run.context.child_runs.get("wf-node")
        assert child_run_id, (
            f"No child run for wf-node; got {parent_run.context.child_runs}"
        )
        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child should be COMPLETED for inline scenario; got {child_run.status}"
        )
        assert parent_run.status == RunStatus.COMPLETED, (
            f"Parent should be COMPLETED (WORKFLOW is leaf); got {parent_run.status}"
        )

    @pytest.mark.asyncio
    async def test_child_run_started_precedes_child_scoped_events(self, deps):
        """Every event whose run_id matches the child must arrive on the
        wire AFTER ``child_run.started`` — otherwise frontend subscribers
        listening on ``run:{child_run_id}`` will miss earlier events.
        """
        self._setup(deps)
        parent_run_id = await _start_parent_run(deps)
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        child_run_id = parent_run.context.child_runs["wf-node"]

        idx_child_started = None
        first_child_event_before_started = None
        for i, (topic, event) in enumerate(deps["event_publisher"].events):
            if topic == "child_run.started" and idx_child_started is None:
                idx_child_started = i
                continue
            event_run_id = getattr(event, "run_id", None)
            if event_run_id == child_run_id:
                if idx_child_started is None and first_child_event_before_started is None:
                    first_child_event_before_started = (i, topic)

        assert idx_child_started is not None, (
            f"No child_run.started event; topics: "
            f"{deps['event_publisher'].topics()}"
        )
        assert first_child_event_before_started is None, (
            f"Child-scoped event {first_child_event_before_started} arrived "
            f"before child_run.started (idx={idx_child_started}). "
            f"Topics: {deps['event_publisher'].topics()}"
        )

    @pytest.mark.asyncio
    async def test_child_run_completed_precedes_parent_run_completed(self, deps):
        """The child run's terminal event must reach the wire before the
        parent run's terminal event. Both classify as TERMINAL, so this
        relies on insertion-sequence preservation within the TERMINAL lane.
        """
        self._setup(deps)
        parent_run_id = await _start_parent_run(deps)
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        child_run_id = parent_run.context.child_runs["wf-node"]

        idx_child_run_completed = None
        idx_parent_run_completed = None
        for i, (topic, event) in enumerate(deps["event_publisher"].events):
            # Unified run lifecycle topic: run.status + status discriminator.
            if str(topic) != "run.status" or getattr(event, "status", "") != "completed":
                continue
            event_run_id = getattr(event, "run_id", None)
            if event_run_id == child_run_id and idx_child_run_completed is None:
                idx_child_run_completed = i
            elif event_run_id == parent_run_id and idx_parent_run_completed is None:
                idx_parent_run_completed = i

        assert idx_child_run_completed is not None, (
            f"No child run.status(completed) event; topics: "
            f"{deps['event_publisher'].topics()}"
        )
        assert idx_parent_run_completed is not None, (
            f"No parent run.status(completed) event; topics: "
            f"{deps['event_publisher'].topics()}"
        )
        assert idx_child_run_completed < idx_parent_run_completed, (
            f"Child run.completed (idx={idx_child_run_completed}) must "
            f"precede parent run.completed (idx={idx_parent_run_completed}). "
            f"Topics: {deps['event_publisher'].topics()}"
        )

    @pytest.mark.asyncio
    async def test_child_task_dispatched_precedes_child_task_completed(self, deps):
        """For the child's inline node, its node.dispatched must arrive
        on the wire before its node.completed.

        Currently node.dispatched is IMMEDIATE and node.completed is
        NORMAL, so all dispatched events drain before any completed
        events — that's the property we assert.
        """
        self._setup(deps)
        parent_run_id = await _start_parent_run(deps)
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        child_run_id = parent_run.context.child_runs["wf-node"]

        idx_dispatched = None
        idx_completed = None
        for i, (topic, event) in enumerate(deps["event_publisher"].events):
            event_run_id = getattr(event, "run_id", None)
            event_node_id = getattr(event, "node_id", None)
            if (event_run_id == child_run_id
                    and event_node_id == "child-trigger"):
                if topic == "node.dispatched" and idx_dispatched is None:
                    idx_dispatched = i
                if topic == "node.completed" and idx_completed is None:
                    idx_completed = i

        assert idx_dispatched is not None, (
            f"No node.dispatched for child-trigger; topics: "
            f"{deps['event_publisher'].topics()}"
        )
        assert idx_completed is not None, (
            f"No node.completed for child-trigger; topics: "
            f"{deps['event_publisher'].topics()}"
        )
        assert idx_dispatched < idx_completed, (
            f"node.dispatched (idx={idx_dispatched}) must precede "
            f"node.completed (idx={idx_completed}) for the same task. "
            f"Topics: {deps['event_publisher'].topics()}"
        )

    @pytest.mark.asyncio
    async def test_parent_workflow_handoff_precedes_child_run_started(self, deps):
        """The parent WORKFLOW node's ``node.input`` handoff must precede
        the child_run.started — same property as the INPUT case, verified
        here for the inline-node variant. (node.dispatched is emitted
        after the child bootstrap under uniform execution.)
        """
        self._setup(deps)
        await _start_parent_run(deps)

        wf_input_idx = None
        child_started_idx = None
        for i, (topic, event) in enumerate(deps["event_publisher"].events):
            node_id = getattr(event, "node_id", None)
            if topic == "node.input" and node_id == "wf-node" and wf_input_idx is None:
                wf_input_idx = i
            if topic == "child_run.started" and child_started_idx is None:
                child_started_idx = i

        assert wf_input_idx is not None, "No node.input for the WORKFLOW node"
        assert child_started_idx is not None, "No child_run.started captured"
        assert wf_input_idx < child_started_idx, (
            f"WORKFLOW node.input (idx={wf_input_idx}) must precede "
            f"child_run.started (idx={child_started_idx}). "
            f"Topics: {deps['event_publisher'].topics()}"
        )

    @pytest.mark.asyncio
    async def test_full_wire_order_snapshot(self, deps):
        """Snapshot of the full wire order under uniform execution. If this
        fails, the order changed — review whether the causal constraints
        above still hold before re-pinning the sequence.
        """
        self._setup(deps)
        parent_run_id = await _start_parent_run(deps)
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        child_run_id = parent_run.context.child_runs["wf-node"]

        def _scope(event):
            run_id = getattr(event, "run_id", None)
            if run_id == parent_run_id:
                return "parent"
            if run_id == child_run_id:
                return "child"
            return ""

        summary = [
            (
                str(topic),
                _scope(event),
                getattr(event, "node_id", "") or "",
                getattr(event, "status", "") or "",
            )
            for topic, event in deps["event_publisher"].events
        ]
        causal_view = "\n".join(
            f"  {i:2d}. {t:25s} scope={sc:6s} node={n:14s} status={st}"
            for i, (t, sc, n, st) in enumerate(summary)
        )

        expected = [
            ("run.status", "parent", "", "started"),
            ("node.output.produced", "parent", "__bootstrap__", ""),
            ("node.input", "parent", "trigger", ""),
            ("node.dispatched", "parent", "trigger", ""),
            ("node.output.produced", "parent", "trigger", ""),
            ("node.completed", "parent", "trigger", "COMPLETED"),
            ("node.input", "parent", "wf-node", ""),
            ("child_run.started", "", "", ""),
            ("node.dispatched", "parent", "wf-node", ""),
            ("run.status", "child", "", "started"),
            ("node.output.produced", "child", "__bootstrap__", ""),
            ("node.input", "child", "child-trigger", ""),
            ("node.dispatched", "child", "child-trigger", ""),
            ("node.output.produced", "child", "child-trigger", ""),
            ("node.completed", "child", "child-trigger", "COMPLETED"),
            ("run.status", "child", "", "completed"),
            ("node.output.produced", "parent", "wf-node", ""),
            ("node.completed", "parent", "wf-node", "COMPLETED"),
            ("child_run.completed", "", "", ""),
            ("run.status", "parent", "", "completed"),
        ]
        assert summary == expected, (
            f"Wire order changed.\nFull wire order:\n{causal_view}"
        )
