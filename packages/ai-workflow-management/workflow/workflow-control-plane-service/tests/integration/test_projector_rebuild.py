"""Projector rebuild test — Phase 4b.3.

Drives a real workflow through ``OrchestrationEngine`` with the Phase
4b.1 event log wiring, then rebuilds the run state by replaying the
log through the Phase 4b.3 ``rebuild_run_state`` reducer, and asserts
the rebuilt state matches the live ``WorkflowRun`` state on the
fields the projector owns today.

This test is the **regression safety net** for Phase 5. Any change
that breaks the "event log == live structural state" property on the
lifecycle subset will fail this test. Phase 5 (event-driven BUILTIN)
will extend the assertions to include ``variables`` once node outputs
flow through events.

Scope today:
- **Happy path** — TRIGGER → COMPUTE completes successfully. The
  projector's ``status``, ``completed_nodes``, ``skipped_nodes`` must
  match the live run, and calling rebuild twice must produce identical
  state (replay determinism).
- **Wipe-then-rebuild** — verifies ``clear`` + replay composes and
  replay is scoped per-run.
- **Cascade-skip (Phase 4b.4)** — TRIGGER → COMPUTE(success→ok,
  failure→err) branches. After Phase 4b.4 migrated the post-handler
  BUILTIN edge loop to ``compute_fanout``, COMPUTE's success port
  filters correctly AND cascade-skips the failure-branch subtree.
  This test exercises the full chain: engine → log → projector.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.orchestration.loop_driver.event_driven_loop_driver import EventDrivenLoopDriver
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.projection import rebuild_run_state
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
from app.layer2_application.interfaces.orchestration_queue_interface import (
    ExecuteNodeAction,
    StartRunAction,
)
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer4_frameworks.messaging.event_log_appending_publisher import (
    EventLogAppendingPublisher,
)
from app.layer2_application.orchestration_queue.memory_queue import MemoryOrchestrationQueue
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository


async def _drain_queue(engine: OrchestrationEngine, queue: MemoryOrchestrationQueue, max_steps: int = 100) -> int:
    """Drain the queue by dispatching actions to the engine."""
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
    return steps


def _build_engine():
    """Build a fully wired engine with a real event log decorator."""
    event_log_repo = MemoryEventLogRepository()
    inner_publisher = MagicMock()
    event_publisher = EventLogAppendingPublisher(
        inner=inner_publisher,
        event_log_repo=event_log_repo,
    )

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

    handlers = NodeHandlerRegistry()
    handlers.register(NodeType.TRIGGER, TriggerNodeHandler())
    handlers.register(NodeType.COMPUTE, ComputeNodeHandler(resolver))

    task_factory = TaskFactory()
    successor_processor = SuccessorProcessor(
        handlers, task_factory, data_store_service=data_store,
    )

    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    workflow_repo = MemoryWorkflowRepository()

    stage_recorder = StageRecorder(MagicMock())
    stage_recorder.record = MagicMock()

    driver = EventDrivenLoopDriver(resolver)

    engine = OrchestrationEngine(
        handler_registry=handlers,
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
        event_driven_loop_driver=driver,
    )

    # Set up the queue for event-driven execution
    queue = MemoryOrchestrationQueue()
    engine.set_queue(queue)

    return {
        "engine": engine,
        "queue": queue,
        "event_log_repo": event_log_repo,
        "run_repo": run_repo,
        "workflow_repo": workflow_repo,
    }


@pytest.mark.asyncio
async def test_rebuild_matches_live_state_for_happy_path_run():
    """Simple TRIGGER → COMPUTE workflow. After it completes:

    - live run has ``status=COMPLETED``, ``completed_nodes=
      {trigger-1, compute-1}``, ``skipped_nodes=set()``
    - projector rebuild from the event log must match on all three
    """
    deps = _build_engine()
    engine = deps["engine"]
    queue = deps["queue"]
    workflow_repo = deps["workflow_repo"]
    run_repo = deps["run_repo"]
    event_log_repo = deps["event_log_repo"]

    workflow = Workflow(
        id="wf-rebuild-1",
        name="rebuild-happy",
        version="1",
        nodes=[
            WorkflowNode(id="trigger-1", name="trigger", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="compute-1",
                name="double",
                node_type=NodeType.COMPUTE,
                config={"expression": "21 * 2"},
            ),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="compute-1"),
        ],
    )
    workflow_repo.save(workflow)

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-rebuild-1",
        workflow_id=workflow.id,
        status=RunStatus.PENDING,
        context=RunContext(variables={}, topology=topology),
    )
    run_repo.save(run)

    await engine.orchestrate_start(run.id)
    # Drain the queue to process all enqueued actions
    await _drain_queue(engine, queue)

    # ── live state (ground truth) ────────────────────────────────
    live_run = run_repo.get_by_id(run.id)
    assert live_run.status == RunStatus.COMPLETED, (
        f"Sanity: happy-path workflow should reach COMPLETED; got {live_run.status}"
    )
    assert "trigger-1" in live_run.context.completed_nodes
    assert "compute-1" in live_run.context.completed_nodes

    # ── projector rebuild ───────────────────────────────────────
    projected = rebuild_run_state(run.id, event_log_repo)

    # The reducer-owned fields must match the live state.
    assert projected.status == "COMPLETED", (
        f"projected status {projected.status} != live COMPLETED — "
        f"the event log is missing RunCompleted or the reducer doesn't "
        f"handle it"
    )
    assert projected.completed_nodes == frozenset(live_run.context.completed_nodes), (
        f"projected completed_nodes {sorted(projected.completed_nodes)} != "
        f"live {sorted(live_run.context.completed_nodes)} — "
        f"a NodeCompleted event is missing from the log or the reducer "
        f"is dropping it"
    )
    assert projected.skipped_nodes == frozenset(live_run.context.skipped_nodes)
    assert projected.workflow_id == live_run.workflow_id or projected.workflow_id == ""

    # Phase 3b.1: Variables are no longer written to live run.context.variables.
    # The engine emits NodeOutputProduced events and the projector rebuilds
    # variables from the event log. Verify projected state has variables.
    assert projected.variables, (
        "projected state should have variables rebuilt from NodeOutputProduced events"
    )
    # Expected variables based on COMPUTE node output
    assert "compute-1" in projected.variables, (
        f"projected variables missing compute-1; got keys {list(projected.variables.keys())}"
    )

    # Determinism — calling rebuild a second time yields identical state.
    projected_again = rebuild_run_state(run.id, event_log_repo)
    assert projected == projected_again


@pytest.mark.asyncio
async def test_rebuild_parity_for_port_filtered_branch():
    """Port filtering under edge-based dispatch + projector rebuild parity.

    Workflow: TRIGGER → COMPUTE(success→ok, failure→err). COMPUTE
    succeeds, so only the success→ok edge is traversed.

    SA-846 retired the downstream cascade-skip BFS (see
    ``SuccessorProcessor.process_uniform``): a port-filtered branch is
    simply NEVER DISPATCHED — no task, no TaskSkipped/NodeCancelled
    event, no ``skipped_nodes`` entry. Skips are recorded only when
    flow-derived (PARALLEL gated branches, child-fail). Run completion
    tolerates the undispatched branch via the leaves-covered check.

    The invariant this test protects is REBUILD PARITY: the projector
    rebuild of the event log must agree exactly with the live aggregate
    for completed_nodes, skipped_nodes, and run status — for the
    port-filter case specifically, both must show err-branch as
    untouched (neither completed nor skipped) while the run COMPLETES.
    """
    deps = _build_engine()
    engine = deps["engine"]
    queue = deps["queue"]
    workflow_repo = deps["workflow_repo"]
    run_repo = deps["run_repo"]
    event_log_repo = deps["event_log_repo"]

    workflow = Workflow(
        id="wf-cascade-skip",
        name="cascade-skip-branching",
        version="1",
        nodes=[
            WorkflowNode(id="trigger-1", name="trigger", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="compute-1",
                name="always_ok",
                node_type=NodeType.COMPUTE,
                config={"expression": "1"},
            ),
            WorkflowNode(
                id="ok-branch",
                name="ok",
                node_type=NodeType.COMPUTE,
                config={"expression": "2"},
            ),
            WorkflowNode(
                id="err-branch",
                name="err",
                node_type=NodeType.COMPUTE,
                config={"expression": "99"},
            ),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="compute-1"),
            WorkflowEdge(
                id="e2",
                source_node_id="compute-1",
                target_node_id="ok-branch",
                source_port_id="success",
            ),
            WorkflowEdge(
                id="e3",
                source_node_id="compute-1",
                target_node_id="err-branch",
                source_port_id="failure",
            ),
        ],
    )
    workflow_repo.save(workflow)

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-cascade-skip-1",
        workflow_id=workflow.id,
        status=RunStatus.PENDING,
        context=RunContext(variables={}, topology=topology),
    )
    run_repo.save(run)

    await engine.orchestrate_start(run.id)
    # Drain the queue to process all enqueued actions
    await _drain_queue(engine, queue)

    # ── live state — success branch ran; failure branch never dispatched
    live_run = run_repo.get_by_id(run.id)
    assert "ok-branch" in live_run.context.completed_nodes, (
        "ok-branch should have run after COMPUTE takes success port"
    )
    assert "err-branch" not in live_run.context.completed_nodes
    assert "err-branch" not in live_run.context.skipped_nodes, (
        "edge-based dispatch records no skip for port-filtered branches "
        "— if one appears, the cascade-skip BFS was reintroduced; review "
        "against the SA-846 taken-edge design before changing this test"
    )
    # The undispatched branch must not block completion.
    assert live_run.status == RunStatus.COMPLETED

    # ── projector rebuild — matches live state exactly (the invariant)
    projected = rebuild_run_state(run.id, event_log_repo)
    assert "ok-branch" in projected.completed_nodes
    assert "err-branch" not in projected.completed_nodes
    assert "err-branch" not in projected.skipped_nodes
    assert projected.completed_nodes == frozenset(live_run.context.completed_nodes)
    assert projected.skipped_nodes == frozenset(live_run.context.skipped_nodes)


@pytest.mark.asyncio
async def test_rebuild_after_wipe_still_matches_live_state():
    """After running a workflow, clear the log's run once and rebuild:
    the rebuilt state should match the zero-state (PENDING + empty sets),
    proving ``clear`` + ``rebuild`` compose correctly. Then run a second
    workflow under the same repo and confirm the new run's projection
    isn't contaminated by the first run's events.

    This is a Phase 5 pre-requisite: the Projector rebuild pass will
    wipe + replay, and this test asserts wipe works and replay is
    scoped per-run.
    """
    deps = _build_engine()
    engine = deps["engine"]
    queue = deps["queue"]
    workflow_repo = deps["workflow_repo"]
    run_repo = deps["run_repo"]
    event_log_repo = deps["event_log_repo"]

    workflow = Workflow(
        id="wf-rebuild-wipe",
        name="rebuild-wipe",
        version="1",
        nodes=[
            WorkflowNode(id="trigger-1", name="trigger", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="compute-1",
                name="double",
                node_type=NodeType.COMPUTE,
                config={"expression": "3 + 4"},
            ),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="compute-1"),
        ],
    )
    workflow_repo.save(workflow)

    topology = GraphTopology.from_workflow(workflow)
    run_a = WorkflowRun(
        id="run-wipe-a",
        workflow_id=workflow.id,
        status=RunStatus.PENDING,
        context=RunContext(variables={}, topology=topology),
    )
    run_repo.save(run_a)
    await engine.orchestrate_start(run_a.id)
    # Drain the queue to process all enqueued actions
    await _drain_queue(engine, queue)

    # First rebuild — populated.
    first = rebuild_run_state(run_a.id, event_log_repo)
    assert first.status == "COMPLETED"
    assert "compute-1" in first.completed_nodes
    assert "compute-1" in first.variables, (
        "Phase 5.1: variables should round-trip via NodeOutputProduced"
    )

    # Wipe the run's log.
    removed = event_log_repo.clear(run_a.id)
    assert removed > 0

    # After wipe, rebuild returns the initial state.
    after_wipe = rebuild_run_state(run_a.id, event_log_repo)
    from app.layer2_application.projection import ProjectedRunState
    assert after_wipe == ProjectedRunState(run_id=run_a.id)

    # A second run on the same repo should be unaffected.
    run_b = WorkflowRun(
        id="run-wipe-b",
        workflow_id=workflow.id,
        status=RunStatus.PENDING,
        context=RunContext(
            variables={},
            topology=GraphTopology.from_workflow(workflow),
        ),
    )
    run_repo.save(run_b)
    await engine.orchestrate_start(run_b.id)
    # Drain the queue to process all enqueued actions
    await _drain_queue(engine, queue)

    b_projected = rebuild_run_state(run_b.id, event_log_repo)
    assert b_projected.status == "COMPLETED"
    assert "compute-1" in b_projected.completed_nodes
    # The wiped run's slot is still empty.
    assert rebuild_run_state(run_a.id, event_log_repo) == ProjectedRunState(run_id=run_a.id)
