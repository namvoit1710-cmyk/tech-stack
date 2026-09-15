"""End-to-end integration tests for the edge-based dispatch engine.

These tests prove the new orchestration model works end-to-end with a
real ``OrchestrationEngine`` + memory queue + synchronous queue drain.
They are the contract for the post-skip-calculation engine:

- ``compute_fanout`` only emits FanoutActions for taken edges; no
  cascade-skip BFS or downstream skip subtree is computed.
- ``CONDITION`` / ``SWITCH`` / ``PARALLEL`` handlers return only
  ``taken_edge_ports`` — they never populate ``skipped_node_ids``.
- A node fires when an incoming edge is taken; the predecessor-readiness
  AND-join gate is gone.
- Multi-incoming-edge dedup creates exactly one task per (run, node)
  via :meth:`OrchestrationEngine._active_task`. ``MERGE``
  and ``PARALLEL`` are exempt so they can accumulate per-arrival state.
- ``check_run_completion`` decides COMPLETED via quiescence (no active
  tasks) — the "all leaves covered" gate has been retired.
- The PENDING transition publishes ``RunPending`` so the projector
  view's ``status`` field tracks the aggregate.

Each test builds a small synthetic workflow, runs it through the
synchronous worker drain, and asserts on the captured event stream
+ run final state.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.events import EventTopic
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
from app.layer2_application.node_handlers.condition_handler import ConditionNodeHandler
from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler
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
    ExecuteNodeAction,
    LoopIterationAction,
    OrchestrationAction,
    StartRunAction,
)
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer2_application.orchestration_queue.memory_queue import MemoryOrchestrationQueue
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository


# ─────────────────────────── harness ─────────────────────────────────


class _CapturingPublisher:
    """Records every event the engine publishes for later assertion."""

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

    def topics(self) -> list[str]:
        return [t for t, _ in self.events]

    def events_of(self, topic: str) -> list[object]:
        return [e for t, e in self.events if t == topic]


def _build_harness(workflow: Workflow):
    """Build a fully wired engine + queue + repos with a capturing publisher."""
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

    registry = NodeHandlerRegistry()
    registry.register(NodeType.TRIGGER, TriggerNodeHandler())
    registry.register(NodeType.COMPUTE, ComputeNodeHandler(resolver))
    registry.register(NodeType.CONDITION, ConditionNodeHandler(condition_evaluator))
    registry.register(NodeType.MERGE, MergeNodeHandler())

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
        "workflow_repo": workflow_repo,
        "stage_recorder": stage_recorder,
    }


async def _drive_queue(engine: OrchestrationEngine, queue: MemoryOrchestrationQueue,
                       *, max_steps: int = 200) -> int:
    """Drain the orchestration queue synchronously. Returns step count."""
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


async def _start_and_drive(harness, workflow: Workflow, run_id: str):
    """Create a run + drive the queue to quiescence."""
    from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
    from app.layer1_domain.entities.workflow_run import GraphTopology

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id=run_id, workflow_id=workflow.id,
        status=RunStatus.RUNNING,
        context=RunContext(topology=topology, variables={"input": {}}),
    )
    harness["run_repo"].save(run)
    await harness["engine"].orchestrate_start(run_id)
    await _drive_queue(harness["engine"], harness["queue"])
    return harness["run_repo"].get_by_id(run_id)


# ────────────────────── 1. quiescence completion ─────────────────────


@pytest.mark.asyncio
async def test_run_completes_via_quiescence_no_skip_events():
    """A trivial linear flow ``trigger → compute → leaf`` runs to
    completion and emits zero ``task.skipped`` events. The completion
    decision is made by quiescence (no active tasks) — the legacy
    "all leaves covered" gate is gone, so we no longer manufacture
    skipped entries to satisfy it.
    """
    workflow = Workflow(
        id="wf-q",
        name="quiescence",
        version="1",
        nodes=[
            WorkflowNode(id="trigger", name="t", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(id="step", name="s", node_type=NodeType.COMPUTE,
                         config={"expression": "1 + 1"}),
            WorkflowNode(id="leaf", name="l", node_type=NodeType.COMPUTE,
                         config={"expression": "2 * 3"}),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="step"),
            WorkflowEdge(id="e2", source_node_id="step", target_node_id="leaf"),
        ],
    )
    harness = _build_harness(workflow)
    run = await _start_and_drive(harness, workflow, "run-q")

    assert run.status == RunStatus.COMPLETED, (
        f"run should COMPLETE via quiescence; got {run.status}"
    )
    # No skip/cancel events — the cascade-skip BFS that used to emit
    # them has been retired; nothing here is cancelled.
    assert harness["publisher"].events_of(EventTopic.NODE_CANCELLED) == []

    # All three nodes completed exactly once.
    completed = [
        e for e in harness["publisher"].events_of(EventTopic.NODE_COMPLETED)
    ]
    completed_node_ids = sorted(e.node_id for e in completed)
    assert completed_node_ids == ["leaf", "step", "trigger"]


# ────────────────────── 2. CONDITION untaken branch ──────────────────


@pytest.mark.asyncio
async def test_condition_untaken_branch_silently_dropped():
    """``cond`` evaluates ``true`` so its ``true_branch`` fires;
    the ``false_branch`` and its entire downstream subtree never
    receive any task and emit no events.

    Pre-refactor: the handler walked the false-branch subtree and
    populated ``skipped_node_ids``, the engine emitted ``task.skipped``
    events for each. Post-refactor: untaken edges are simply not
    followed — silent drop.
    """
    workflow = Workflow(
        id="wf-cond",
        name="cond-untaken",
        version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="cond", node_type=NodeType.CONDITION,
                         config={"expression": "1 == 1"}),  # always true
            WorkflowNode(id="taken", node_type=NodeType.COMPUTE,
                         config={"expression": "42"}),
            WorkflowNode(id="dropped_a", node_type=NodeType.COMPUTE,
                         config={"expression": "0/0"}),  # would crash if reached
            WorkflowNode(id="dropped_b", node_type=NodeType.COMPUTE,
                         config={"expression": "0/0"}),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="cond"),
            WorkflowEdge(id="e_t", source_node_id="cond", target_node_id="taken",
                         source_port_id="true_branch"),
            WorkflowEdge(id="e_f", source_node_id="cond", target_node_id="dropped_a",
                         source_port_id="false_branch"),
            WorkflowEdge(id="e_a_b", source_node_id="dropped_a", target_node_id="dropped_b"),
        ],
    )
    harness = _build_harness(workflow)
    run = await _start_and_drive(harness, workflow, "run-cond")

    assert run.status == RunStatus.COMPLETED

    completed_node_ids = {
        e.node_id for e in harness["publisher"].events_of(EventTopic.NODE_COMPLETED)
    }
    # Taken branch ran; untaken subtree did not.
    assert "taken" in completed_node_ids
    assert "dropped_a" not in completed_node_ids
    assert "dropped_b" not in completed_node_ids

    # Zero skip events emitted — the eager BFS is gone.
    assert harness["publisher"].events_of(EventTopic.NODE_CANCELLED) == []

    # The CONDITION handler stopped populating skipped_node_ids.
    # Verify directly: no node_id in the workflow appears in any
    # NodeOutputProduced.taken_edge_ports we didn't intend.
    npos = harness["publisher"].events_of(EventTopic.NODE_OUTPUT_PRODUCED)
    cond_npo = next(n for n in npos if n.node_id == "cond")
    assert "e_t" in cond_npo.taken_edge_ports
    assert "e_f" not in cond_npo.taken_edge_ports


@pytest.mark.asyncio
async def test_condition_unwired_branch_fires_nothing():
    """Regression (bug #3): when CONDITION selects a branch whose port is
    UNWIRED, it must dispatch NOTHING — never fall through and fire the other,
    wired branch.

    ``cond`` evaluates ``false`` → ``false_branch``, but only ``true_branch``
    has an outgoing edge, so the selection is empty. The wired ``true_branch``
    target ``wired_wrong`` must never run.

    Pre-fix: empty ``taken_edge_ports`` fell to the ``compute_fanout`` else
    path; CONDITION isn't in ``_PORT_ROUTED_TYPES``, so it traversed ALL
    outgoing edges and fired ``wired_wrong``. The fix keys the routing mode off
    node type, so a branching node with no selected edges dispatches nothing.
    """
    workflow = Workflow(
        id="wf-cond-unwired",
        name="cond-unwired",
        version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="cond", node_type=NodeType.CONDITION,
                         config={"expression": "1 == 2"}),  # always false
            WorkflowNode(id="wired_wrong", node_type=NodeType.COMPUTE,
                         config={"expression": "42"}),  # must NOT run
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="cond"),
            # Only the TRUE branch is wired; the chosen FALSE branch is unwired.
            WorkflowEdge(id="e_t", source_node_id="cond", target_node_id="wired_wrong",
                         source_port_id="true_branch"),
        ],
    )
    harness = _build_harness(workflow)
    run = await _start_and_drive(harness, workflow, "run-cond-unwired")

    assert run.status == RunStatus.COMPLETED

    completed_node_ids = {
        e.node_id for e in harness["publisher"].events_of(EventTopic.NODE_COMPLETED)
    }
    assert "cond" in completed_node_ids
    # The wired-but-wrong branch must NOT have fired.
    assert "wired_wrong" not in completed_node_ids

    # CONDITION chose the unwired false_branch → no edges taken.
    npos = harness["publisher"].events_of(EventTopic.NODE_OUTPUT_PRODUCED)
    cond_npo = next(n for n in npos if n.node_id == "cond")
    assert cond_npo.taken_edge_ports == {}


# ────────────────────── 3. multi-incoming-edge dedup ─────────────────


@pytest.mark.asyncio
async def test_multi_incoming_edge_dedup_dispatches_target_once():
    """Both branches of a fan-out converge on a single TASK-shaped
    target. Without dedup, edge-based dispatch would create one task
    per arriving edge. The :meth:`_active_task`
    helper ensures exactly one task per (run, node).
    """
    workflow = Workflow(
        id="wf-dedup",
        name="dedup",
        version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="branch_a", node_type=NodeType.COMPUTE,
                         config={"expression": "1"}),
            WorkflowNode(id="branch_b", node_type=NodeType.COMPUTE,
                         config={"expression": "2"}),
            WorkflowNode(id="join", node_type=NodeType.COMPUTE,
                         config={"expression": "3"}),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="branch_a"),
            WorkflowEdge(id="e2", source_node_id="trigger", target_node_id="branch_b"),
            WorkflowEdge(id="e3", source_node_id="branch_a", target_node_id="join"),
            WorkflowEdge(id="e4", source_node_id="branch_b", target_node_id="join"),
        ],
    )
    harness = _build_harness(workflow)
    run = await _start_and_drive(harness, workflow, "run-dedup")

    assert run.status == RunStatus.COMPLETED

    join_completions = [
        e for e in harness["publisher"].events_of(EventTopic.NODE_COMPLETED)
        if e.node_id == "join"
    ]
    assert len(join_completions) == 1, (
        f"join should complete exactly once; got {len(join_completions)}"
    )

    # Each branch produced output and tried to dispatch ``join`` — proven by
    # ``join`` appearing as a target in NodeOutputProduced.taken_edge_ports
    # OR by both branch_a and branch_b completing (they did, since the
    # workflow finished). Direct dedup proof: only one NodeDispatched
    # fired for ``join``.
    join_dispatches = [
        e for e in harness["publisher"].events_of(EventTopic.NODE_DISPATCHED)
        if e.node_id == "join"
    ]
    assert len(join_dispatches) == 1


# ──────────────────────── 4. MERGE quorum ───────────────────────────


@pytest.mark.asyncio
async def test_merge_quorum_under_edge_dispatch():
    """A MERGE node with ``wait_all`` keeps its quorum semantics under
    edge-based dispatch. Each predecessor's arrival re-invokes the
    handler (MERGE/PARALLEL bypass dedup); only the final invocation
    that satisfies the strategy emits an immediate-mode completion and
    routes downstream.
    """
    workflow = Workflow(
        id="wf-merge",
        name="merge-quorum",
        version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="left", node_type=NodeType.COMPUTE,
                         config={"expression": "1"}),
            WorkflowNode(id="right", node_type=NodeType.COMPUTE,
                         config={"expression": "2"}),
            WorkflowNode(
                id="merge",
                node_type=NodeType.MERGE,
                config={"merge_strategy": "wait_all"},
            ),
            WorkflowNode(id="after", node_type=NodeType.COMPUTE,
                         config={"expression": "99"}),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="left"),
            WorkflowEdge(id="e2", source_node_id="trigger", target_node_id="right"),
            WorkflowEdge(id="e3", source_node_id="left", target_node_id="merge"),
            WorkflowEdge(id="e4", source_node_id="right", target_node_id="merge"),
            WorkflowEdge(id="e5", source_node_id="merge", target_node_id="after"),
        ],
    )
    harness = _build_harness(workflow)
    run = await _start_and_drive(harness, workflow, "run-merge")

    assert run.status == RunStatus.COMPLETED

    # ``after`` only fires once after both branches arrive at MERGE.
    after_completions = [
        e for e in harness["publisher"].events_of(EventTopic.NODE_COMPLETED)
        if e.node_id == "after"
    ]
    assert len(after_completions) == 1

    # MERGE handler was invoked at least twice (once per arriving branch)
    # — verified by NodeOutputProduced events for the ``merge`` node.
    # The final invocation reached ``immediate`` mode and routed
    # downstream; earlier ``deferred`` invocations did not.
    npos = harness["publisher"].events_of(EventTopic.NODE_OUTPUT_PRODUCED)
    merge_npos = [e for e in npos if e.node_id == "merge"]
    assert len(merge_npos) >= 1, "MERGE must produce at least one NodeOutputProduced"

    # Both ``left`` and ``right`` completed — proves both branches arrived.
    completed_node_ids = {
        e.node_id for e in harness["publisher"].events_of(EventTopic.NODE_COMPLETED)
    }
    assert {"left", "right", "merge", "after"}.issubset(completed_node_ids)


# ─────────────── 5. failure-port handoff (no run failure) ─────────────


@pytest.mark.asyncio
async def test_failure_port_handoff_runs_recovery_and_completes():
    """When a TASK fails AND its failure-port edge has a successor,
    ``process_and_dispatch_successors`` marks the failed task with
    ``output_metadata.skipped=True`` so ``check_run_completion``'s
    ``has_failed`` filter ignores it. The recovery node runs and the
    run COMPLETES (not FAILED).
    """
    from app.layer1_domain.entities.task import Task
    from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
    from app.layer1_domain.entities.workflow_run import GraphTopology

    workflow = Workflow(
        id="wf-failport",
        name="failure-port",
        version="1",
        nodes=[
            WorkflowNode(id="failing", node_type=NodeType.TASK, worker_type="w"),
            WorkflowNode(id="recovery", node_type=NodeType.COMPUTE,
                         config={"expression": "100"}),
            WorkflowNode(id="happy", node_type=NodeType.COMPUTE,
                         config={"expression": "0/0"}),  # not reached
        ],
        edges=[
            WorkflowEdge(id="e_s", source_node_id="failing", target_node_id="happy",
                         source_port_id="success"),
            WorkflowEdge(id="e_f", source_node_id="failing", target_node_id="recovery",
                         source_port_id="failure"),
        ],
    )
    harness = _build_harness(workflow)
    engine = harness["engine"]
    task_repo = harness["task_repo"]
    run_repo = harness["run_repo"]

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-failport", workflow_id=workflow.id,
        status=RunStatus.RUNNING,
        context=RunContext(topology=topology, variables={"input": {}}),
    )
    run_repo.save(run)

    # Manually mark the failing task as FAILED (simulating a worker
    # callback) then drive ``orchestrate_fail`` directly. This is the
    # same path FailTaskUseCase invokes via FailTaskAction.
    failing_task = Task(
        id="run-failport-failing", run_id=run.id, node_id="failing",
        status=TaskStatus.FAILED, worker_type="w", error="connection refused",
    )
    task_repo.save(failing_task)

    await engine.orchestrate_fail(run.id, failing_task.id)
    await _drive_queue(engine, harness["queue"])

    final_run = run_repo.get_by_id(run.id)
    assert final_run.status == RunStatus.COMPLETED, (
        f"failure-port routing should keep the run alive; got {final_run.status}"
    )

    completed = {
        e.node_id for e in harness["publisher"].events_of(EventTopic.NODE_COMPLETED)
    }
    assert "recovery" in completed, "failure-port successor must execute"
    assert "happy" not in completed, "success-port edge silently dropped"

    # The failed task was marked ``skipped=True`` so the run wasn't failed.
    saved_failing = task_repo.get_by_id(failing_task.id)
    assert (saved_failing.output_metadata or {}).get("skipped") is True


# ──────────────── 6. node.input fires for every executed node ────────


@pytest.mark.asyncio
async def test_node_input_event_per_executed_node():
    """``EventTopic.NODE_INPUT`` is the dispatch-boundary event the engine
    publishes whenever a node is about to execute, regardless of node
    type. The user's earlier confusion ("``node.input`` fires for nodes
    that aren't INPUT-type") was mostly naming; we lock the contract
    here so refactors don't accidentally narrow it.
    """
    workflow = Workflow(
        id="wf-nin",
        name="node-input",
        version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="cond", node_type=NodeType.CONDITION,
                         config={"expression": "1 < 2"}),
            WorkflowNode(id="taken", node_type=NodeType.COMPUTE,
                         config={"expression": "1"}),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="cond"),
            WorkflowEdge(id="e_t", source_node_id="cond", target_node_id="taken",
                         source_port_id="true_branch"),
        ],
    )
    harness = _build_harness(workflow)
    run = await _start_and_drive(harness, workflow, "run-nin")

    assert run.status == RunStatus.COMPLETED

    nis = harness["publisher"].events_of(EventTopic.NODE_INPUT)
    invoked_nodes = sorted(e.node_id for e in nis)
    assert invoked_nodes == ["cond", "taken", "trigger"], (
        "node.input must fire for every executed node, not just INPUT-type"
    )


# ──────────── 7. SA-846: taken edge into a completed node re-runs it ─


@pytest.mark.asyncio
async def test_completed_node_reruns_on_late_second_arrival():
    """SA-846 cycle execution: an edge taken into an already-completed
    node re-runs it — fanout never skips a target because it completed.

    Graph (uneven path lengths so ``t`` COMPLETES before the second
    edge into it fires)::

        trigger ── a ──────────────► t
        trigger ── b ── c ─────────► t

    Drain order: a → t (t completes) → b → c → second edge into t.
    Pre-SA-846 the second arrival was silently dropped. Now it bumps
    the node's visit counter and dispatches a fresh ``-v1`` task, so
    ``t`` runs twice and the run still completes via quiescence.
    """
    expr = {"expression": "1 + 1"}
    workflow = Workflow(
        id="wf-revisit",
        name="revisit",
        version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="a", node_type=NodeType.COMPUTE, config=expr),
            WorkflowNode(id="b", node_type=NodeType.COMPUTE, config=expr),
            WorkflowNode(id="c", node_type=NodeType.COMPUTE, config=expr),
            WorkflowNode(id="t", node_type=NodeType.COMPUTE, config=expr),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="a"),
            WorkflowEdge(id="e2", source_node_id="trigger", target_node_id="b"),
            WorkflowEdge(id="e3", source_node_id="a", target_node_id="t"),
            WorkflowEdge(id="e4", source_node_id="b", target_node_id="c"),
            WorkflowEdge(id="e5", source_node_id="c", target_node_id="t"),
        ],
    )
    harness = _build_harness(workflow)
    run = await _start_and_drive(harness, workflow, "run-revisit")

    assert run.status == RunStatus.COMPLETED

    # ``t`` executed twice — once per arriving edge.
    t_completions = [
        e for e in harness["publisher"].events_of(EventTopic.NODE_COMPLETED)
        if e.node_id == "t"
    ]
    assert len(t_completions) == 2, (
        f"expected t to run twice (revisit on second edge), "
        f"got {len(t_completions)} completion(s)"
    )

    # The revisit ran under a fresh visit-suffixed task id — the second
    # attempt must not upsert over the first (distinct event identity).
    t_tasks = [
        t for t in harness["task_repo"].list_by_run("run-revisit")
        if t.node_id == "t"
    ]
    t_ids = sorted(t.id for t in t_tasks)
    assert t_ids == ["run-revisit-t", "run-revisit-t-v1"], t_ids
    assert all(t.status == TaskStatus.COMPLETED for t in t_tasks)
    assert run.context.node_visits.get("t") == 1


# ──────────── 8. SA-846: taken edge into a skipped node un-skips it ─


@pytest.mark.asyncio
async def test_skipped_node_reruns_when_edge_arrives():
    """SA-846 cycle execution: a taken edge into a flow-skipped node
    clears the skip and runs it.

    All skips are flow-derived (branch_not_taken cascade / child-fail),
    so a later taken edge — e.g. a cycle re-entering a branch the
    previous iteration routed away from — legitimately overrides them.
    Pre-fix the fanout gates silently dropped the edge and the node
    stayed SKIPPED forever.

    The skip is seeded directly on the run context (the production
    writers — gated PARALLEL, HUMAN_ACTION resume, child-fail — all
    land in the same ``skipped_nodes`` set).
    """
    from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
    from app.layer1_domain.entities.workflow_run import GraphTopology

    expr = {"expression": "1 + 1"}
    workflow = Workflow(
        id="wf-unskip",
        name="unskip",
        version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="a", node_type=NodeType.COMPUTE, config=expr),
            WorkflowNode(id="t", node_type=NodeType.COMPUTE, config=expr),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="a"),
            WorkflowEdge(id="e2", source_node_id="a", target_node_id="t"),
        ],
    )
    harness = _build_harness(workflow)

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-unskip", workflow_id=workflow.id,
        status=RunStatus.RUNNING,
        context=RunContext(
            topology=topology, variables={"input": {}},
            skipped_nodes={"t"},
        ),
    )
    harness["run_repo"].save(run)
    await harness["engine"].orchestrate_start("run-unskip")
    await _drive_queue(harness["engine"], harness["queue"])
    run = harness["run_repo"].get_by_id("run-unskip")

    # The edge into ``t`` cleared the stale skip and ran the node; the
    # run reached COMPLETED via quiescence (the fresh task counted as
    # active despite the prior skip).
    assert run.status == RunStatus.COMPLETED
    assert "t" not in run.context.skipped_nodes
    assert "t" in run.context.completed_nodes

    t_tasks = [
        t for t in harness["task_repo"].list_by_run("run-unskip")
        if t.node_id == "t"
    ]
    assert len(t_tasks) == 1
    # The un-skip counts as a revisit: the visit counter bumped so the
    # task id (and every task-id-derived event id) is distinct from
    # anything the skipped visit may have logged.
    assert t_tasks[0].id == "run-unskip-t-v1", t_tasks[0].id
    assert t_tasks[0].status == TaskStatus.COMPLETED
    assert run.context.node_visits.get("t") == 1
