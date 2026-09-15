"""End-to-end integration tests for Phase 5.2.2 queued-mode event-driven loops.

These tests prove the queued-mode loop mechanism works end-to-end
with a real ``OrchestrationEngine`` + ``EventDrivenLoopDriver`` +
``MemoryOrchestrationQueue``. They drive the worker loop synchronously
by dequeuing actions one at a time and calling the matching
``orchestrate_execute_node`` / ``orchestrate_loop_iteration`` /
``orchestrate_start`` method directly — same test harness pattern as
``test_queued_builtin_state_updates.py``.

Scenarios covered:
- 3-item sequential LOOP with a single-node body — proves the basic
  mechanism (init → body exec → LoopIterationAction → advance → ...).
- 2-item LOOP with a multi-node body (body-entry COMPUTE → leaf
  COMPUTE) — proves the iter_progress runtime BFS correctly tracks
  multi-node body subgraphs.
- 2-item LOOP with the user's diamond body shape: CONDITION →
  (true → HTTP) / (false → COMPUTE → HTTP). Proves cascade-skip
  merging into iter_progress works for CONDITION-gated bodies.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import GraphTopology, RunContext, WorkflowRun
from app.layer1_domain.events import (
    LoopCompleted,
    LoopIterationCompleted,
    LoopIterationStarted,
    LoopStarted,
)
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.orchestration.loop_driver import EventDrivenLoopDriver
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
from app.layer2_application.node_handlers.condition_handler import ConditionNodeHandler
from app.layer2_application.node_handlers.loop_handler import LoopNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
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
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.interfaces.orchestration_queue_interface import (
    ContinueRunAction,
    ExecuteNodeAction,
    LoopIterationAction,
    OrchestrationAction,
    StartRunAction,
)
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer2_application.orchestration_queue.memory_queue import (
    MemoryOrchestrationQueue,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository


# ──────────────────────────── harness ─────────────────────────────────


class _CapturingPublisher:
    """Minimal IEventPublisher stub that records every published event."""

    def __init__(self):
        self.events: list = []

    def publish(self, topic, event):
        self.events.append((topic, event))

    def publish_with_headers(self, topic, event, *, headers=None, key=None):
        self.events.append((topic, event))


def _build_engine_with_queue_and_driver(workflow: Workflow):
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
    condition_evaluator = ConditionEvaluator(resolver)

    handler_registry = NodeHandlerRegistry()
    handler_registry.register(NodeType.TRIGGER, TriggerNodeHandler())
    handler_registry.register(NodeType.COMPUTE, ComputeNodeHandler(resolver))
    handler_registry.register(NodeType.CONDITION, ConditionNodeHandler(condition_evaluator))
    handler_registry.register(
        NodeType.LOOP,
        LoopNodeHandler(variable_resolver=resolver, event_driven=True),
    )

    task_factory = TaskFactory()
    successor_processor = SuccessorProcessor(
        handler_registry, task_factory, data_store_service=data_store,
    )

    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    workflow_repo = MemoryWorkflowRepository()
    workflow_repo.save(workflow)

    stage_recorder = StageRecorder(MagicMock())
    stage_recorder.record = MagicMock()

    capturing = _CapturingPublisher()
    driver = EventDrivenLoopDriver(resolver)

    engine = OrchestrationEngine(
        handler_registry=handler_registry,
        task_factory=task_factory,
        successor_processor=successor_processor,
        stage_recorder=stage_recorder,
        task_repo=task_repo,
        run_repo=run_repo,
        event_publisher=capturing,
        file_service_client=file_client,
        csv_converter=csv_converter,
        workflow_repo=workflow_repo,
        variable_resolver=resolver,
        data_store_service=data_store,
        event_driven_loop_driver=driver,
    )

    queue = MemoryOrchestrationQueue()
    engine.set_queue(queue)

    return {
        "engine": engine,
        "queue": queue,
        "capturing": capturing,
        "workflow_repo": workflow_repo,
        "run_repo": run_repo,
        "task_repo": task_repo,
        "task_factory": task_factory,
        "data_store": data_store,
    }


async def _drive_queue_to_completion(
    engine: OrchestrationEngine,
    queue: MemoryOrchestrationQueue,
    run_id: str,
    *,
    max_steps: int = 200,
) -> int:
    """Drive the worker loop synchronously by dequeuing actions one
    at a time. Stops when the queue is empty or the run reaches a
    terminal state. Returns the number of actions processed.

    Uses ``asyncio.wait_for`` with a short timeout on ``dequeue`` so
    the test doesn't hang if the loop stalls (which would indicate a
    Phase 5.2.2 bug).
    """
    import asyncio
    steps = 0
    while steps < max_steps:
        try:
            action = await asyncio.wait_for(queue.dequeue(), timeout=0.05)
        except asyncio.TimeoutError:
            return steps  # queue drained
        steps += 1
        await _dispatch_action(engine, action)
    return steps


async def _dispatch_action(
    engine: OrchestrationEngine, action: OrchestrationAction,
) -> None:
    """Dispatch a single orchestration action to the engine — the same
    switch the OrchestrationWorker uses."""
    if isinstance(action, StartRunAction):
        await engine.orchestrate_start(action.run_id)
    elif isinstance(action, ExecuteNodeAction):
        await engine.orchestrate_execute_node(action.run_id, action.task_id)
    elif isinstance(action, LoopIterationAction):
        await engine.orchestrate_loop_iteration(
            action.run_id, action.task_id, action.loop_node_id, action.iteration_index,
        )
    elif isinstance(action, ContinueRunAction):
        await engine.orchestrate_continue(action.run_id, action.task_id)
    else:
        raise AssertionError(f"unexpected action type: {type(action).__name__}")


# ────────────── Scenario 1: single-node body ──────────────────────


def _build_single_node_body_loop() -> Workflow:
    """LOOP(collection=[1,2,3]) → COMPUTE body → loop; exit → END-node (COMPUTE)."""
    trigger = WorkflowNode(id="trigger", name="trigger", node_type=NodeType.TRIGGER)
    loop_node = WorkflowNode(
        id="loop", name="loop", node_type=NodeType.LOOP,
        config={"collection": "{{$input.items}}", "mode": "sequential"},
    )
    body = WorkflowNode(
        id="body", name="body", node_type=NodeType.COMPUTE,
        config={"expression": "1 + 1", "result_key": "body_out"},
    )
    after = WorkflowNode(
        id="after", name="after", node_type=NodeType.COMPUTE,
        config={"expression": "99"},
    )
    return Workflow(
        id="wf-5221", name="wf-5221", version="1",
        nodes=[trigger, loop_node, body, after],
        edges=[
            WorkflowEdge(id="e-trigger-loop", source_node_id="trigger", target_node_id="loop"),
            WorkflowEdge(id="e-body", source_node_id="loop", target_node_id="body", condition="body"),
            WorkflowEdge(id="e-exit", source_node_id="loop", target_node_id="after", condition="exit"),
        ],
    )


@pytest.mark.asyncio
async def test_queued_single_node_body_loop_runs_all_iterations():
    """3-item sequential LOOP in queued mode runs all three iterations
    via LoopIterationAction dispatch — proves the Phase 5.2.2 mechanism
    works end-to-end."""
    workflow = _build_single_node_body_loop()
    deps = _build_engine_with_queue_and_driver(workflow)
    engine = deps["engine"]
    queue = deps["queue"]

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-1", workflow_id=workflow.id,
        status=RunStatus.RUNNING,
        input_data={"items": [1, 2, 3]},
        context=RunContext(
            variables={"input": {"items": [1, 2, 3]}},
            topology=topology,
        ),
    )
    deps["run_repo"].save(run)

    # Manually bootstrap: mark TRIGGER completed and dispatch LOOP directly
    # (orchestrate_start needs start_run_use_case plumbing we don't have here).
    run.context.completed_nodes.add("trigger")
    loop_task = deps["task_factory"].create_task(workflow.get_node("loop"), run)
    deps["task_repo"].save(loop_task)
    await queue.enqueue(ExecuteNodeAction(run_id=run.id, task_id=loop_task.id))

    # Drive the queue to completion.
    steps = await _drive_queue_to_completion(engine, queue, run.id)

    # Sanity: loop actually ran iterations (not a 1-step dispatch).
    assert steps > 5, f"queue drained too quickly ({steps} steps) — loop may have stalled"

    # Inspect emitted events.
    published = [ev for _topic, ev in deps["capturing"].events]
    loop_started = [e for e in published if isinstance(e, LoopStarted)]
    iter_started = [e for e in published if isinstance(e, LoopIterationStarted)]
    iter_completed = [e for e in published if isinstance(e, LoopIterationCompleted)]
    loop_completed = [e for e in published if isinstance(e, LoopCompleted)]

    assert len(loop_started) == 1, "exactly one LoopStarted"
    assert len(iter_started) == 3, "one LoopIterationStarted per iter"
    assert len(iter_completed) == 3, "one LoopIterationCompleted per iter"
    assert len(loop_completed) == 1, "exactly one LoopCompleted"
    assert loop_completed[0].reason == "all_done"
    assert loop_completed[0].total_iterations == 3

    # Final run state: loop node marked completed.
    final_run = deps["run_repo"].get_by_id(run.id)
    assert "loop" in final_run.context.completed_nodes


# ────────── Regression: worker-body loop advances via orchestrate_continue ──────────


@pytest.mark.asyncio
async def test_worker_body_loop_advances_via_orchestrate_continue():
    """REGRESSION (tenant-1 stall): a WORKER (async) body task completes via
    the worker callback → CompleteTaskUseCase → ContinueRunAction →
    ``orchestrate_continue`` — NOT inline. That path must call
    ``maybe_enqueue_loop_iteration`` or the loop never advances past iteration 0.

    The other tests here use inline COMPUTE/TRIGGER bodies that complete via
    ``orchestrate_execute_node`` (where the hook already fires), so they passed
    while real worker-body loops stalled on tenant-1. This test drives the
    ``orchestrate_continue`` path directly."""
    workflow = _build_single_node_body_loop()
    deps = _build_engine_with_queue_and_driver(workflow)
    engine = deps["engine"]
    queue = deps["queue"]

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-1", workflow_id=workflow.id, status=RunStatus.RUNNING,
        input_data={"items": [1, 2, 3]},
        context=RunContext(variables={"input": {"items": [1, 2, 3]}}, topology=topology),
    )
    deps["run_repo"].save(run)
    run.context.completed_nodes.add("trigger")
    loop_task = deps["task_factory"].create_task(workflow.get_node("loop"), run)
    deps["task_repo"].save(loop_task)

    # Init the loop — dispatches iteration 0's body task.
    await engine.orchestrate_execute_node(run.id, loop_task.id)

    # Grab the body task the loop created; simulate the WORKER completing it
    # asynchronously (mark COMPLETED) — the real async-callback path, which
    # does NOT run the body inline.
    body_tasks = [t for t in deps["task_repo"].list_by_run(run.id) if t.node_id == "body"]
    assert body_tasks, "loop must have dispatched a body task"
    body_task = body_tasks[-1]
    body_task.status = TaskStatus.COMPLETED
    deps["task_repo"].save(body_task)

    # Drain the queue so the body's own ExecuteNodeAction can't advance the loop
    # via the inline path — isolate the callback/continue path.
    import asyncio
    while True:
        try:
            await asyncio.wait_for(queue.dequeue(), timeout=0.02)
        except asyncio.TimeoutError:
            break

    # The worker-callback success path.
    await engine.orchestrate_continue(run.id, body_task.id)

    # The loop MUST have advanced: a LoopIterationAction is now queued.
    queued: list = []
    while True:
        try:
            queued.append(await asyncio.wait_for(queue.dequeue(), timeout=0.02))
        except asyncio.TimeoutError:
            break
    assert any(isinstance(a, LoopIterationAction) for a in queued), (
        "orchestrate_continue must enqueue a LoopIterationAction — without the "
        "loop-advance hook a worker-body loop stalls at iteration 0"
    )


@pytest.mark.asyncio
async def test_maybe_enqueue_gates_on_terminal_status_no_premature_double_fire():
    """The loop-advance hook fires from BOTH the body dispatch site
    (orchestrate_execute_node, where a worker body is only RUNNING) and the
    completion site (orchestrate_continue, COMPLETED). Because iteration_done
    decides purely from iter_progress, a RUNNING-body hook call would advance
    the loop before the body ran AND double-fire with the completion — corrupting
    completed_iterations. The terminal-status gate must make the RUNNING call a
    no-op and the COMPLETED call the single advance."""
    from app.layer1_domain.value_objects.loop_scope import LoopScope

    workflow = _build_single_node_body_loop()
    deps = _build_engine_with_queue_and_driver(workflow)
    engine = deps["engine"]
    queue = deps["queue"]

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-1", workflow_id=workflow.id, status=RunStatus.RUNNING,
        input_data={"items": [1, 2, 3]},
        context=RunContext(variables={"input": {"items": [1, 2, 3]}}, topology=topology),
    )
    # Mid-flight loop_state: iteration 0 in progress, nothing recorded yet.
    run.context.loop_state["loop"] = {
        "items": [1, 2, 3], "total": 3, "current_index": 0,
        "completed_iterations": [], "iter_progress": {0: []},
        "invocation_id": "inv-1", "mode": "sequential", "loop_type": "collection",
    }
    deps["run_repo"].save(run)

    scope = [LoopScope(loop_node_id="loop", invocation_id="inv-1", iter=0, total=3)]

    async def _drain():
        import asyncio
        out = []
        while True:
            try:
                out.append(await asyncio.wait_for(queue.dequeue(), timeout=0.02))
            except asyncio.TimeoutError:
                return out

    # (A) RUNNING body → gate no-ops → NO LoopIterationAction, no progress recorded.
    running = Task(run_id=run.id, node_id="body", status=TaskStatus.RUNNING, scope_stack=scope)
    await engine.maybe_enqueue_loop_iteration(running, run)
    assert not any(isinstance(a, LoopIterationAction) for a in await _drain()), (
        "a RUNNING body must NOT advance the loop (premature/double-fire guard)"
    )
    assert run.context.loop_state["loop"]["iter_progress"][0] == [], (
        "a RUNNING body must not be recorded in iter_progress"
    )

    # (B) COMPLETED body → single LoopIterationAction.
    done = Task(run_id=run.id, node_id="body", status=TaskStatus.COMPLETED, scope_stack=scope)
    await engine.maybe_enqueue_loop_iteration(done, run)
    li = [a for a in await _drain() if isinstance(a, LoopIterationAction)]
    assert len(li) == 1, "a COMPLETED body advances the loop exactly once"


@pytest.mark.asyncio
async def test_orchestrate_iteration_idempotent_on_duplicate_action():
    """The queue is at-least-once: a LoopIterationAction for an already-advanced
    iteration can be redelivered. Advancing again would double-count
    completed_iterations. The idempotency guard must drop the duplicate."""
    workflow = _build_single_node_body_loop()
    deps = _build_engine_with_queue_and_driver(workflow)
    engine = deps["engine"]

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-1", workflow_id=workflow.id, status=RunStatus.RUNNING,
        input_data={"items": [1, 2, 3]},
        context=RunContext(variables={"input": {"items": [1, 2, 3]}}, topology=topology),
    )
    # Iteration 0 already completed.
    run.context.loop_state["loop"] = {
        "items": [1, 2, 3], "total": 3, "current_index": 1,
        "completed_iterations": [0], "iter_progress": {},
        "invocation_id": "inv-1", "mode": "sequential", "loop_type": "collection",
        "enclosing_scope_stack": [],
    }
    deps["run_repo"].save(run)

    before = list(run.context.loop_state["loop"]["completed_iterations"])
    # Duplicate action for the already-done iteration 0.
    await engine.orchestrate_loop_iteration("run-1", "dup-task", "loop", 0)

    after = deps["run_repo"].get_by_id("run-1").context.loop_state["loop"]["completed_iterations"]
    assert after == before == [0], (
        "a duplicate LoopIterationAction must be a no-op — no double-count"
    )
    iter_completed = [
        ev for _t, ev in deps["capturing"].events if isinstance(ev, LoopIterationCompleted)
    ]
    assert iter_completed == [], "duplicate must emit no LoopIterationCompleted"


# ────── Regression: loop node finalizes + run quiesces to COMPLETED ──────


@pytest.mark.asyncio
async def test_loop_node_task_completes_and_run_quiesces():
    """RESIDUAL (tenant-1, live-reproduced): after every iteration + the exit
    node finish, the loop node stayed READY and the run stayed RUNNING forever.

    Root cause: ``_run_loop_body`` leaves the LOOP node's own tracking task at
    READY (it only ships the body tasks), and the all-done branch only did
    ``completed_nodes.add(loop)`` — it never drove the loop task terminal. Since
    both the canvas read path (``aggregate_node_status``) AND run finalization
    (``decide_completion``) are TASK-based, a READY loop task keeps the run
    non-quiescent indefinitely.

    The older scenario tests only asserted ``"loop" in completed_nodes`` (the
    in-memory aggregate line 339 always set) — never the loop TASK status or
    ``run.status`` — which is why they were green while tenant-1 hung. This test
    asserts the two things that actually gate the user-visible outcome."""
    workflow = _build_single_node_body_loop()
    deps = _build_engine_with_queue_and_driver(workflow)
    engine = deps["engine"]
    queue = deps["queue"]

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-1", workflow_id=workflow.id, status=RunStatus.RUNNING,
        input_data={"items": [1, 2, 3]},
        context=RunContext(variables={"input": {"items": [1, 2, 3]}}, topology=topology),
    )
    deps["run_repo"].save(run)
    run.context.completed_nodes.add("trigger")
    loop_task = deps["task_factory"].create_task(workflow.get_node("loop"), run)
    deps["task_repo"].save(loop_task)
    await queue.enqueue(ExecuteNodeAction(run_id=run.id, task_id=loop_task.id))

    await _drive_queue_to_completion(engine, queue, run.id)

    # (1) The loop node's tracking task is terminal — the direct fix. Without it
    # the loop task stays READY and the canvas shows the loop node stuck READY.
    final_loop_tasks = [
        t for t in deps["task_repo"].list_by_run(run.id) if t.node_id == "loop"
    ]
    assert final_loop_tasks, "loop node must have a tracking task"
    assert all(t.status == TaskStatus.COMPLETED for t in final_loop_tasks), (
        "the loop node's tracking task must be COMPLETED once all iterations "
        f"finish — got {[t.status for t in final_loop_tasks]}"
    )

    # (2) The run quiesces to COMPLETED. A non-terminal loop task would keep
    # decide_completion returning 'none' and the run stuck RUNNING forever.
    final_run = deps["run_repo"].get_by_id(run.id)
    assert final_run.status == RunStatus.COMPLETED, (
        f"run must finalize to COMPLETED — got {final_run.status}; a READY loop "
        "task keeps the run non-quiescent (the residual finalization bug)"
    )

    # A NodeCompleted was emitted for the loop node (the event log + projector
    # view stay honest; NodeCompleted does not re-route the exit edge).
    from app.layer1_domain.events import NodeCompleted
    loop_node_completed = [
        ev for _t, ev in deps["capturing"].events
        if isinstance(ev, NodeCompleted) and ev.node_id == "loop"
    ]
    assert loop_node_completed, "loop node completion must emit NodeCompleted"


@pytest.mark.asyncio
async def test_empty_collection_loop_completes_and_run_quiesces():
    """Edge case: a LOOP over an EMPTY collection (0 items) must still finalize
    the loop node + quiesce the run + run the exit node.

    This path does NOT go through ``orchestrate_iteration`` (there are no
    iterations) — ``initialize_loop`` short-circuits, and the handler sets
    ``completion_mode='immediate'`` because ``total == 0``. ``_run_immediate``
    marks the loop task COMPLETED and routes out the ``loop_complete`` port to
    the exit node. This test pins that behaviour so the empty case can't
    regress into the same 'loop stuck READY / run stuck RUNNING' residual the
    non-empty path had."""
    workflow = _build_single_node_body_loop()
    deps = _build_engine_with_queue_and_driver(workflow)
    engine = deps["engine"]
    queue = deps["queue"]

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-1", workflow_id=workflow.id, status=RunStatus.RUNNING,
        input_data={"items": []},
        context=RunContext(variables={"input": {"items": []}}, topology=topology),
    )
    deps["run_repo"].save(run)
    run.context.completed_nodes.add("trigger")
    loop_task = deps["task_factory"].create_task(workflow.get_node("loop"), run)
    deps["task_repo"].save(loop_task)
    await queue.enqueue(ExecuteNodeAction(run_id=run.id, task_id=loop_task.id))

    await _drive_queue_to_completion(engine, queue, run.id)

    # No iterations ran, but the loop was entered + completed.
    published = [ev for _t, ev in deps["capturing"].events]
    assert len([e for e in published if isinstance(e, LoopStarted)]) == 1
    assert [e for e in published if isinstance(e, LoopIterationStarted)] == []
    loop_completed = [e for e in published if isinstance(e, LoopCompleted)]
    assert len(loop_completed) == 1 and loop_completed[0].total_iterations == 0

    # The loop node's tracking task is terminal — not left READY.
    final_loop_tasks = [
        t for t in deps["task_repo"].list_by_run(run.id) if t.node_id == "loop"
    ]
    assert final_loop_tasks and all(
        t.status == TaskStatus.COMPLETED for t in final_loop_tasks
    ), f"empty loop task must be COMPLETED — got {[t.status for t in final_loop_tasks]}"

    # The exit node ran and the run quiesced to COMPLETED.
    after_tasks = [
        t for t in deps["task_repo"].list_by_run(run.id) if t.node_id == "after"
    ]
    assert after_tasks and all(t.status == TaskStatus.COMPLETED for t in after_tasks), (
        "the loop's exit node must run even when the collection is empty"
    )
    final_run = deps["run_repo"].get_by_id(run.id)
    assert final_run.status == RunStatus.COMPLETED, (
        f"empty-collection loop must finalize the run — got {final_run.status}"
    )


# ────────────── Scenario 2: multi-node body (linear) ──────────────


def _build_multi_node_body_loop() -> Workflow:
    """LOOP([a,b]) → body_entry (COMPUTE) → body_leaf (COMPUTE) → (no exit path); exit → after."""
    trigger = WorkflowNode(id="trigger", name="trigger", node_type=NodeType.TRIGGER)
    loop_node = WorkflowNode(
        id="loop", name="loop", node_type=NodeType.LOOP,
        config={"collection": "{{$input.items}}", "mode": "sequential"},
    )
    body_entry = WorkflowNode(
        id="body_a", name="body_a", node_type=NodeType.COMPUTE,
        config={"expression": "1"},
    )
    body_leaf = WorkflowNode(
        id="body_b", name="body_b", node_type=NodeType.COMPUTE,
        config={"expression": "2"},
    )
    after = WorkflowNode(
        id="after", name="after", node_type=NodeType.COMPUTE,
        config={"expression": "3"},
    )
    return Workflow(
        id="wf-5222", name="wf-5222", version="1",
        nodes=[trigger, loop_node, body_entry, body_leaf, after],
        edges=[
            WorkflowEdge(id="e-trigger-loop", source_node_id="trigger", target_node_id="loop"),
            WorkflowEdge(id="e-body", source_node_id="loop", target_node_id="body_a", condition="body"),
            WorkflowEdge(id="e-ab", source_node_id="body_a", target_node_id="body_b"),
            WorkflowEdge(id="e-exit", source_node_id="loop", target_node_id="after", condition="exit"),
        ],
    )


@pytest.mark.asyncio
async def test_queued_multi_node_body_loop_advances_via_iter_progress():
    """Multi-node body (body_entry → body_leaf). The iter_progress
    runtime BFS must wait for BOTH body nodes to complete per iter
    before enqueuing LoopIterationAction. Proves the Phase 5.2.2
    subgraph-tracking mechanism without a TopologyBuilder precompute."""
    workflow = _build_multi_node_body_loop()
    deps = _build_engine_with_queue_and_driver(workflow)
    engine = deps["engine"]
    queue = deps["queue"]

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-1", workflow_id=workflow.id,
        status=RunStatus.RUNNING,
        input_data={"items": ["x", "y"]},
        context=RunContext(
            variables={"input": {"items": ["x", "y"]}},
            topology=topology,
        ),
    )
    deps["run_repo"].save(run)

    run.context.completed_nodes.add("trigger")
    loop_task = deps["task_factory"].create_task(workflow.get_node("loop"), run)
    deps["task_repo"].save(loop_task)
    await queue.enqueue(ExecuteNodeAction(run_id=run.id, task_id=loop_task.id))

    steps = await _drive_queue_to_completion(engine, queue, run.id)
    assert steps > 5, f"queue drained too quickly ({steps} steps)"

    published = [ev for _topic, ev in deps["capturing"].events]
    iter_completed = [e for e in published if isinstance(e, LoopIterationCompleted)]
    loop_completed = [e for e in published if isinstance(e, LoopCompleted)]

    # 2 iterations, each with a multi-node body.
    assert len(iter_completed) == 2
    assert len(loop_completed) == 1
    assert loop_completed[0].reason == "all_done"
    assert loop_completed[0].total_iterations == 2

    final_run = deps["run_repo"].get_by_id(run.id)
    assert "loop" in final_run.context.completed_nodes


# ────────────── Scenario 3: CONDITION-gated body ──────────────────


def _build_condition_body_loop() -> Workflow:
    """LOOP([0,1]) → CONDITION → (true → compute_t) / (false → compute_f).

    Both branches merge at a shared 'after' node OUTSIDE the loop (via
    the exit edge). This tests cascade-skip bookkeeping: when CONDITION
    goes true, compute_f is skipped and must count toward iter_progress
    via the run.context.skipped_nodes sweep.
    """
    trigger = WorkflowNode(id="trigger", name="trigger", node_type=NodeType.TRIGGER)
    loop_node = WorkflowNode(
        id="loop", name="loop", node_type=NodeType.LOOP,
        config={"collection": "{{$input.items}}", "mode": "sequential"},
    )
    cond = WorkflowNode(
        id="cond", name="cond", node_type=NodeType.CONDITION,
        config={"expression": "{{$loop.index}} = 0"},
    )
    compute_t = WorkflowNode(
        id="compute_t", name="compute_t", node_type=NodeType.COMPUTE,
        config={"expression": "100"},
    )
    compute_f = WorkflowNode(
        id="compute_f", name="compute_f", node_type=NodeType.COMPUTE,
        config={"expression": "200"},
    )
    after = WorkflowNode(
        id="after", name="after", node_type=NodeType.COMPUTE,
        config={"expression": "999"},
    )
    return Workflow(
        id="wf-5223", name="wf-5223", version="1",
        nodes=[trigger, loop_node, cond, compute_t, compute_f, after],
        edges=[
            WorkflowEdge(id="e-trigger-loop", source_node_id="trigger", target_node_id="loop"),
            WorkflowEdge(id="e-body", source_node_id="loop", target_node_id="cond", condition="body"),
            WorkflowEdge(
                id="e-true",
                source_node_id="cond",
                target_node_id="compute_t",
                source_port_id="true_branch",
            ),
            WorkflowEdge(
                id="e-false",
                source_node_id="cond",
                target_node_id="compute_f",
                source_port_id="false_branch",
            ),
            WorkflowEdge(id="e-exit", source_node_id="loop", target_node_id="after", condition="exit"),
        ],
    )


@pytest.mark.asyncio
async def test_queued_condition_body_loop_advances_via_cascade_skip():
    """Body has a CONDITION that takes different branches per iter.
    The iter_progress bookkeeping + cascade-skip sweep must correctly
    advance the loop even though only one branch runs per iteration."""
    workflow = _build_condition_body_loop()
    deps = _build_engine_with_queue_and_driver(workflow)
    engine = deps["engine"]
    queue = deps["queue"]

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-1", workflow_id=workflow.id,
        status=RunStatus.RUNNING,
        input_data={"items": [0, 1]},
        context=RunContext(
            variables={"input": {"items": [0, 1]}},
            topology=topology,
        ),
    )
    deps["run_repo"].save(run)

    run.context.completed_nodes.add("trigger")
    loop_task = deps["task_factory"].create_task(workflow.get_node("loop"), run)
    deps["task_repo"].save(loop_task)
    await queue.enqueue(ExecuteNodeAction(run_id=run.id, task_id=loop_task.id))

    steps = await _drive_queue_to_completion(engine, queue, run.id)
    assert steps > 5, f"queue drained too quickly ({steps} steps)"

    published = [ev for _topic, ev in deps["capturing"].events]
    iter_completed = [e for e in published if isinstance(e, LoopIterationCompleted)]
    loop_completed = [e for e in published if isinstance(e, LoopCompleted)]

    # 2 iterations → 2 LoopIterationCompleted + 1 LoopCompleted.
    assert len(iter_completed) == 2
    assert len(loop_completed) == 1
    assert loop_completed[0].reason == "all_done"
    assert loop_completed[0].total_iterations == 2

    final_run = deps["run_repo"].get_by_id(run.id)
    assert "loop" in final_run.context.completed_nodes
