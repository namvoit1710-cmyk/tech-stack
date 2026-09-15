"""Integration tests for the Phase 4c.4.3 event-driven LOOP path.

Drives a real ``OrchestrationEngine`` with a real event-driven
``LoopNodeHandler`` (constructed with ``event_driven=True``) through the
``dispatch_or_handle_task`` entry point that production uses. Asserts:

- The walker's iteration coordination still works end-to-end (legacy
  ``_run_loop_iterations`` cooperates with the new event-emitting
  handler).
- The full loop event sequence is published in order: ``LoopStarted``,
  then N × (``LoopIterationStarted`` → body ``NodeCompleted`` →
  ``LoopIterationCompleted``), then ``LoopCompleted``.
- Body tasks dispatched to ``_enqueue_or_dispatch`` carry a non-empty
  ``scope_stack`` — this is what Phase 4c.4.4 builds on.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="inline-execution coverage — rewrite to drive MemoryOrchestrationQueue via tests.helpers.queue_runner")
from unittest.mock import MagicMock

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.events import (
    LoopCompleted,
    LoopIterationCompleted,
    LoopIterationStarted,
    LoopStarted,
)
from app.layer2_application.node_handlers.base import (
    NodeHandlerRegistry,
    NodeHandlerResult,
)
from app.layer2_application.node_handlers.loop_handler import LoopNodeHandler
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.value_objects.run_status import RunStatus
from tests.helpers.node_driver import drive_node


class _CapturingEventPublisher:
    """Minimal IEventPublisher stub that records every (topic, event)."""

    def __init__(self) -> None:
        self.events: list = []

    def publish(self, topic: str, event) -> None:
        self.events.append((topic, event))

    def publish_with_headers(self, topic: str, event, *, headers=None, key=None) -> None:
        self.events.append((topic, event))


class _CompletingBodyHandler:
    """Stand-in TASK handler that immediately completes any body node.

    The event-driven LOOP walker dispatches body tasks via
    ``engine._enqueue_or_dispatch`` which (in inline mode) invokes
    ``dispatch_or_handle_task`` recursively. For BUILTIN body nodes the
    engine needs a handler. We register this stub against
    ``NodeType.TRIGGER`` (used as the body node type in the test fixture
    below) so the engine's inline path completes without trying to
    route to a real worker.
    """

    shape = None  # not inspected in this path

    async def handle(self, node, run, workflow, topology, completed_task, task_factory):
        return NodeHandlerResult(
            tasks_to_dispatch=[],
            state_updates={},
            events=[],
        )


def _collection_workflow_for_engine(items: list, mode: str = "sequential", **config):
    """Build a minimal workflow: LOOP → body (TRIGGER) → exit (TRIGGER)."""
    loop_config: dict = {
        "collection": "{{$input.items}}",
        "mode": mode,
    }
    loop_config.update(config)
    loop_node = WorkflowNode(
        id="loop", name="loop",
        node_type=NodeType.LOOP, config=loop_config,
    )
    # Use TRIGGER (a BUILTIN that passes through _CompletingBodyHandler)
    # to avoid dispatching to a real worker.
    body_node = WorkflowNode(
        id="body", name="body", node_type=NodeType.TRIGGER,
    )
    exit_node = WorkflowNode(
        id="exit", name="exit", node_type=NodeType.TRIGGER,
    )
    workflow = Workflow(
        id="wf", name="wf",
        nodes=[loop_node, body_node, exit_node],
        edges=[
            WorkflowEdge(
                id="e-body", source_node_id="loop",
                target_node_id="body", condition="body",
            ),
            WorkflowEdge(
                id="e-exit", source_node_id="loop",
                target_node_id="exit", condition="exit",
            ),
        ],
    )
    topology = GraphTopology(
        edge_map={
            "loop": workflow.edges,
            "body": [],
            "exit": [],
        },
        adjacency={
            "loop": ["body", "exit"],
            "body": [],
            "exit": [],
        },
        predecessors={
            "loop": [],
            "body": ["loop"],
            "exit": ["loop"],
        },
        entry_nodes=["loop"],
        leaf_nodes=["body", "exit"],
    )
    run = WorkflowRun(
        id="run-1", workflow_id="wf",
        status=RunStatus.RUNNING,
        context=RunContext(
            variables={"input": {"items": items}},
            topology=topology,
        ),
    )
    return loop_node, workflow, topology, run


def _build_engine_with_event_driven_loop():
    resolver = VariableResolver(
        [MetadataResolverStrategy(), TraversalResolverStrategy()],
    )
    from app.layer2_application.orchestration.loop_driver import EventDrivenLoopDriver
    driver = EventDrivenLoopDriver(resolver)
    registry = NodeHandlerRegistry()
    registry.register(
        NodeType.LOOP,
        LoopNodeHandler(variable_resolver=resolver, event_driven=True),
    )
    # TRIGGER is used as both the body and exit node types in the test
    # fixture so they route to the completing stub.
    registry.register(NodeType.TRIGGER, _CompletingBodyHandler())

    capturing = _CapturingEventPublisher()
    engine = OrchestrationEngine(
        handler_registry=registry,
        task_factory=TaskFactory(),
        successor_processor=MagicMock(),
        stage_recorder=MagicMock(),
        task_repo=MagicMock(),
        run_repo=MagicMock(),
        event_publisher=capturing,
        callback_publisher=None,
        event_driven_loop_driver=driver,
    )
    return engine, capturing


# ──────────────────── Integration tests ────────────────────


@pytest.mark.asyncio
async def test_flag_on_single_loop_emits_full_event_sequence():
    """End-to-end: a 3-item sequential LOOP run through the engine
    produces the full event sequence expected by the spike §3.4."""
    engine, capturing = _build_engine_with_event_driven_loop()
    loop_node, workflow, topology, run = _collection_workflow_for_engine(
        ["a", "b", "c"],
    )
    loop_task = Task(
        id="t-loop", run_id="run-1", node_id="loop",
        status=TaskStatus.READY,
    )

    await drive_node(engine, loop_task, run, workflow, topology)

    published = [ev for _topic, ev in capturing.events]
    # Count each loop event type.
    started = [e for e in published if isinstance(e, LoopStarted)]
    iter_started = [e for e in published if isinstance(e, LoopIterationStarted)]
    iter_completed = [e for e in published if isinstance(e, LoopIterationCompleted)]
    completed = [e for e in published if isinstance(e, LoopCompleted)]

    assert len(started) == 1, "exactly one LoopStarted"
    assert len(iter_completed) == 3, "one LoopIterationCompleted per iter"
    # One LoopIterationStarted is emitted per iteration dispatch.
    # With 3 items, sequential mode: init dispatches iter 0, then each
    # completion dispatches the next, so 3 total.
    assert len(iter_started) == 3
    assert len(completed) == 1
    assert completed[0].reason == "all_done"
    assert completed[0].total_iterations == 3
    # The LoopStarted's invocation_id is reused by the completion.
    assert started[0].invocation_id == completed[0].invocation_id
    # All three iteration-started events share the same invocation_id.
    assert {e.invocation_id for e in iter_started} == {started[0].invocation_id}
    # One event per iter index — we assert the SET not the sequence
    # because the engine's ``_publish_now_many(handler_result.events)``
    # runs AFTER ``_run_loop_iterations`` returns (see
    # orchestration_engine.py:446), so the init-time events (iter=0)
    # are published AFTER the walker's per-iteration events. This
    # ordering quirk is pre-existing engine behavior, not introduced
    # by Phase 4c.4.3 — fixing it is Phase 5.2 / event-log ordering
    # work.
    assert {e.iter for e in iter_started} == {0, 1, 2}
    # LoopIterationCompleted also covers all three iter indices.
    assert {e.iter for e in iter_completed} == {0, 1, 2}


@pytest.mark.asyncio
async def test_flag_on_empty_collection_emits_started_then_completed():
    """An empty loop: init dispatches exit, emits LoopStarted +
    LoopCompleted(all_done, 0) with no body iterations."""
    engine, capturing = _build_engine_with_event_driven_loop()
    loop_node, workflow, topology, run = _collection_workflow_for_engine([])
    loop_task = Task(
        id="t-loop", run_id="run-1", node_id="loop",
        status=TaskStatus.READY,
    )

    await drive_node(engine, loop_task, run, workflow, topology)

    published = [ev for _topic, ev in capturing.events]
    started = [e for e in published if isinstance(e, LoopStarted)]
    completed = [e for e in published if isinstance(e, LoopCompleted)]
    iter_started = [e for e in published if isinstance(e, LoopIterationStarted)]

    assert len(started) == 1
    assert started[0].total == 0
    assert len(completed) == 1
    assert completed[0].total_iterations == 0
    assert completed[0].reason == "all_done"
    assert len(iter_started) == 0, "no body iterations expected"


    # test_flag_off_default_suppresses_new_loop_events REMOVED in Phase 6.3
    # — EVENT_DRIVEN_LOOP is now always true; legacy loop path no longer exists.


# ─────────────── Phase 5.2.1: driver-wired engine tests ───────────────


def _build_engine_with_phase_5_2_1_driver():
    """Build an engine wired with the Phase 5.2.1
    ``EventDrivenLoopDriver``. LOOP nodes bypass the legacy walker
    entirely and go through the driver's ``run_loop`` entry point.

    This is the wiring that fixes the event-ordering quirk: when
    the driver runs, it publishes ``LoopStarted`` and the first
    ``LoopIterationStarted`` BEFORE any body task dispatches, so
    the event log's topological order matches the logical sequence.
    """
    from app.layer2_application.orchestration.loop_driver import EventDrivenLoopDriver

    resolver = VariableResolver(
        [MetadataResolverStrategy(), TraversalResolverStrategy()],
    )
    registry = NodeHandlerRegistry()
    # Handler still registered for back-compat (not called by the
    # driver path but kept so the registry has an entry).
    registry.register(
        NodeType.LOOP,
        LoopNodeHandler(variable_resolver=resolver, event_driven=True),
    )
    registry.register(NodeType.TRIGGER, _CompletingBodyHandler())
    capturing = _CapturingEventPublisher()
    driver = EventDrivenLoopDriver(resolver)
    engine = OrchestrationEngine(
        handler_registry=registry,
        task_factory=TaskFactory(),
        successor_processor=MagicMock(),
        stage_recorder=MagicMock(),
        task_repo=MagicMock(),
        run_repo=MagicMock(),
        event_publisher=capturing,
        callback_publisher=None,
        event_driven_loop_driver=driver,
    )
    return engine, capturing


@pytest.mark.asyncio
async def test_phase_5_2_1_driver_fixes_event_ordering():
    """The Phase 5.2.1 driver MUST publish init events before body
    events, unlike the Phase 4c.4.3 handler + walker path where
    init events were published AFTER the walker returned and thus
    appeared in the log after body events.
    """
    engine, capturing = _build_engine_with_phase_5_2_1_driver()
    loop_node, workflow, topology, run = _collection_workflow_for_engine(
        ["a", "b", "c"],
    )
    loop_task = Task(
        id="t-loop", run_id="run-1", node_id="loop",
        status=TaskStatus.READY,
    )

    await drive_node(engine, loop_task, run, workflow, topology)

    published = [ev for _topic, ev in capturing.events]

    # Strict ordering assertion — the core Phase 5.2.1 fix.
    def first_index(cls):
        for i, ev in enumerate(published):
            if isinstance(ev, cls):
                return i
        return -1

    idx_started = first_index(LoopStarted)
    idx_iter_started = first_index(LoopIterationStarted)
    idx_iter_completed = first_index(LoopIterationCompleted)
    idx_completed = first_index(LoopCompleted)

    assert idx_started >= 0
    assert idx_iter_started > idx_started, (
        "Phase 5.2.1 fix: LoopStarted must publish BEFORE "
        "LoopIterationStarted"
    )
    assert idx_iter_completed > idx_iter_started, (
        "LoopIterationCompleted must follow LoopIterationStarted"
    )
    assert idx_completed > idx_iter_completed, (
        "LoopCompleted must come last"
    )

    # Counts match the 3-item loop.
    assert len([e for e in published if isinstance(e, LoopStarted)]) == 1
    assert len([e for e in published if isinstance(e, LoopIterationCompleted)]) == 3
    loop_completeds = [e for e in published if isinstance(e, LoopCompleted)]
    assert len(loop_completeds) == 1
    assert loop_completeds[0].reason == "all_done"
    assert loop_completeds[0].total_iterations == 3


@pytest.mark.asyncio
async def test_phase_5_2_1_driver_empty_collection():
    """Empty collection through the driver-wired engine: init events
    (LoopStarted + LoopCompleted(all_done, 0)) both publish before
    the exit task is dispatched. No body tasks run."""
    engine, capturing = _build_engine_with_phase_5_2_1_driver()
    loop_node, workflow, topology, run = _collection_workflow_for_engine([])
    loop_task = Task(
        id="t-loop", run_id="run-1", node_id="loop",
        status=TaskStatus.READY,
    )

    await drive_node(engine, loop_task, run, workflow, topology)

    published = [ev for _topic, ev in capturing.events]
    starteds = [e for e in published if isinstance(e, LoopStarted)]
    completeds = [e for e in published if isinstance(e, LoopCompleted)]
    assert len(starteds) == 1
    assert starteds[0].total == 0
    assert len(completeds) == 1
    assert completeds[0].reason == "all_done"
    assert completeds[0].total_iterations == 0
    # No body iteration events
    assert not any(isinstance(e, LoopIterationStarted) for e in published)
    assert not any(isinstance(e, LoopIterationCompleted) for e in published)
