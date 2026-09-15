"""Orchestration scenario audit — fan-out × sub-workflow × revisit × pending.

Queue-driven probes (same harness shape as ``test_workflow_node_revisit``)
for the scenario matrix that had no live coverage after the always-enqueue
StartRun migration left the older child-workflow integration suites stalled
on the engine's undrained default queue:

1. Sub-workflow nested inside a sub-workflow (depth 3 chain).
2. Fan-out into two parallel sub-workflows.
3. Pending INPUT node inside a sub-workflow (parent waits, resume completes
   child AND parent — the SA-846 reentrant child→parent continuation path).
4. A second taken edge arriving at a PENDING INPUT node (coalesce behavior).
5. A late edge re-entering a satisfied n_of_m MERGE (quorum revisit).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.task import TaskStatus
from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.events import EventTopic
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
from app.layer2_application.node_handlers.input_handler import InputNodeHandler
from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.node_handlers.workflow_handler import WorkflowNodeHandler
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer2_application.interfaces.orchestration_queue_interface import (
    ExecuteNodeAction,
    LoopIterationAction,
    ResumeTaskAction,
    StartRunAction,
)
from app.layer2_application.orchestration_queue.memory_queue import MemoryOrchestrationQueue
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository

EXPR = {"expression": "1 + 1"}


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


def _build_harness(*workflows: Workflow):
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
    registry.register(NodeType.INPUT, InputNodeHandler(resolver, data_store_service=data_store))
    registry.register(NodeType.MERGE, MergeNodeHandler())

    task_factory = TaskFactory()
    successor_processor = SuccessorProcessor(
        registry, task_factory, data_store_service=data_store,
    )

    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    workflow_repo = MemoryWorkflowRepository()
    for wf in workflows:
        workflow_repo.save(wf)

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


async def _drive_queue(engine, queue, *, max_steps: int = 300) -> int:
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
        elif isinstance(action, ResumeTaskAction):
            await engine.orchestrate_resume(action.run_id, action.task_id)
        else:
            raise AssertionError(f"unexpected action type: {type(action).__name__}")
    raise AssertionError(f"queue did not drain within {max_steps} steps")


def _start_run(harness, workflow: Workflow, run_id: str) -> WorkflowRun:
    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id=run_id, workflow_id=workflow.id,
        status=RunStatus.RUNNING,
        context=RunContext(topology=topology, variables={"input": {}}),
    )
    harness["run_repo"].save(run)
    return run


def _child_runs_of(harness, parent_run_id: str) -> list[WorkflowRun]:
    return [
        r for r in harness["run_repo"]._store.values()
        if r.parent_run_id == parent_run_id
    ]


async def _resume_pending(harness, run_id: str, task) -> None:
    """Mirror ResumePendingTaskUseCase's contract: complete the task with
    output, then hand continuation to the engine."""
    task.status = TaskStatus.COMPLETED
    task.output_data = {"name": "probe"}
    await harness["task_repo"].asave(task)
    await harness["engine"].orchestrate_resume(run_id, task.id)


# ──────────────────────────────────────────────────────────────────
# 1. Sub-workflow nested in a sub-workflow (depth 3)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nested_sub_workflow_depth3_completes_and_propagates():
    grandchild = Workflow(
        id="gc-wf", name="grandchild", version="1",
        nodes=[WorkflowNode(id="gc-compute", node_type=NodeType.COMPUTE, config=EXPR)],
        edges=[],
    )
    child = Workflow(
        id="c-wf", name="child", version="1",
        nodes=[
            WorkflowNode(id="c-compute", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(
                id="c-w", node_type=NodeType.WORKFLOW,
                config={"workflow_id": "gc-wf"},
            ),
        ],
        edges=[WorkflowEdge(id="ce1", source_node_id="c-compute", target_node_id="c-w")],
    )
    parent = Workflow(
        id="p-wf", name="parent", version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="p-w", node_type=NodeType.WORKFLOW,
                config={"workflow_id": "c-wf"},
            ),
            WorkflowNode(id="p-after", node_type=NodeType.COMPUTE, config=EXPR),
        ],
        edges=[
            WorkflowEdge(id="pe1", source_node_id="trigger", target_node_id="p-w"),
            WorkflowEdge(id="pe2", source_node_id="p-w", target_node_id="p-after"),
        ],
    )
    harness = _build_harness(parent, child, grandchild)
    _start_run(harness, parent, "run-d3")
    await harness["engine"].orchestrate_start("run-d3")
    await _drive_queue(harness["engine"], harness["queue"])

    run = harness["run_repo"].get_by_id("run-d3")
    children = _child_runs_of(harness, "run-d3")
    assert run.status == RunStatus.COMPLETED, f"parent ended {run.status}: {run.error}"
    assert len(children) == 1, f"expected 1 child run, got {[r.id for r in children]}"
    child_run = children[0]
    assert child_run.status == RunStatus.COMPLETED, (
        f"child ended {child_run.status}: {child_run.error}"
    )
    grandchildren = _child_runs_of(harness, child_run.id)
    assert len(grandchildren) == 1, "child did not spawn the grandchild run"
    assert grandchildren[0].status == RunStatus.COMPLETED, (
        f"grandchild ended {grandchildren[0].status}: {grandchildren[0].error}"
    )
    # Depth + root lineage stamped through both hops.
    assert child_run.depth_level == 1
    assert grandchildren[0].depth_level == 2
    assert child_run.root_run_id == "run-d3"
    assert grandchildren[0].root_run_id == "run-d3"


# ──────────────────────────────────────────────────────────────────
# 2. Fan-out into two parallel sub-workflows
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fanout_two_parallel_sub_workflows_both_complete():
    child = Workflow(
        id="fan-child", name="fan-child", version="1",
        nodes=[WorkflowNode(id="fc-compute", node_type=NodeType.COMPUTE, config=EXPR)],
        edges=[],
    )
    parent = Workflow(
        id="fan-parent", name="fan-parent", version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="w1", node_type=NodeType.WORKFLOW,
                config={"workflow_id": "fan-child"},
            ),
            WorkflowNode(
                id="w2", node_type=NodeType.WORKFLOW,
                config={"workflow_id": "fan-child"},
            ),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="w1"),
            WorkflowEdge(id="e2", source_node_id="trigger", target_node_id="w2"),
        ],
    )
    harness = _build_harness(parent, child)
    _start_run(harness, parent, "run-fan")
    await harness["engine"].orchestrate_start("run-fan")
    await _drive_queue(harness["engine"], harness["queue"])

    run = harness["run_repo"].get_by_id("run-fan")
    children = _child_runs_of(harness, "run-fan")
    assert run.status == RunStatus.COMPLETED, f"parent ended {run.status}: {run.error}"
    assert len(children) == 2, (
        f"expected 2 child runs (one per WORKFLOW node), got {[r.id for r in children]}"
    )
    assert all(c.status == RunStatus.COMPLETED for c in children), (
        f"child statuses: {[(c.id, c.status) for c in children]}"
    )
    assert {c.parent_node_id for c in children} == {"w1", "w2"}


# ──────────────────────────────────────────────────────────────────
# 3. Pending INPUT inside a sub-workflow: parent waits, resume cascades
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_input_in_sub_workflow_resume_completes_parent():
    child = Workflow(
        id="pend-child", name="pend-child", version="1",
        nodes=[
            WorkflowNode(
                id="c-input", node_type=NodeType.INPUT,
                config={
                    "instruction": "give input",
                    "input_schema": [{"key": "name", "type": "string", "label": "Name"}],
                },
            ),
        ],
        edges=[],
    )
    parent = Workflow(
        id="pend-parent", name="pend-parent", version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="w", node_type=NodeType.WORKFLOW,
                config={"workflow_id": "pend-child"},
            ),
            WorkflowNode(id="after", node_type=NodeType.COMPUTE, config=EXPR),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="w"),
            WorkflowEdge(id="e2", source_node_id="w", target_node_id="after"),
        ],
    )
    harness = _build_harness(parent, child)
    _start_run(harness, parent, "run-pend")
    await harness["engine"].orchestrate_start("run-pend")
    await _drive_queue(harness["engine"], harness["queue"])

    # Quiesced: child is PENDING on the INPUT node, parent still in flight.
    children = _child_runs_of(harness, "run-pend")
    assert len(children) == 1, f"expected 1 child run, got {[r.id for r in children]}"
    child_run = children[0]
    assert child_run.status == RunStatus.PENDING, (
        f"child should pend on INPUT, got {child_run.status}: {child_run.error}"
    )
    parent_run = harness["run_repo"].get_by_id("run-pend")
    assert parent_run.status == RunStatus.RUNNING, (
        f"parent should keep waiting on the child, got {parent_run.status}"
    )

    pending = [
        t for t in harness["task_repo"].list_by_run(child_run.id)
        if t.node_id == "c-input" and t.status == TaskStatus.PENDING
    ]
    assert len(pending) == 1, (
        f"expected 1 PENDING input task in child, got "
        f"{[(t.id, t.status) for t in harness['task_repo'].list_by_run(child_run.id)]}"
    )

    # Resume the child's INPUT — the continuation must cascade child
    # completion → parent WORKFLOW-node completion → parent completion.
    await _resume_pending(harness, child_run.id, pending[0])
    await _drive_queue(harness["engine"], harness["queue"])

    child_run = harness["run_repo"].get_by_id(child_run.id)
    parent_run = harness["run_repo"].get_by_id("run-pend")
    assert child_run.status == RunStatus.COMPLETED, (
        f"child ended {child_run.status}: {child_run.error}"
    )
    assert parent_run.status == RunStatus.COMPLETED, (
        f"parent ended {parent_run.status}: {parent_run.error} — "
        "child completion did not cascade to the parent"
    )
    after_done = [
        t for t in harness["task_repo"].list_by_run("run-pend")
        if t.node_id == "after" and t.status == TaskStatus.COMPLETED
    ]
    assert after_done, "parent successor after the WORKFLOW node never ran"


# ──────────────────────────────────────────────────────────────────
# 4. Second taken edge into a PENDING INPUT node
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_second_arrival_into_pending_input_defers_revisit():
    """SA-846 Mode A (fixed): while an INPUT task is PENDING (started), a
    second taken edge is recorded as a pending revisit — not dropped. The
    in-flight request is not duplicated (still exactly 1 InputRequested
    while pending); after the first resume the node re-runs once and
    requests input again, and the second resume completes the run."""
    workflow = Workflow(
        id="wf-pend-revisit", name="pend-revisit", version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="a", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(id="b", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(id="c", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(
                id="inp", node_type=NodeType.INPUT,
                config={
                    "instruction": "give input",
                    "input_schema": [{"key": "name", "type": "string", "label": "Name"}],
                },
            ),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="a"),
            WorkflowEdge(id="e2", source_node_id="trigger", target_node_id="b"),
            WorkflowEdge(id="e3", source_node_id="a", target_node_id="inp"),
            WorkflowEdge(id="e4", source_node_id="b", target_node_id="c"),
            WorkflowEdge(id="e5", source_node_id="c", target_node_id="inp"),
        ],
    )
    harness = _build_harness(workflow)
    _start_run(harness, workflow, "run-pr")
    await harness["engine"].orchestrate_start("run-pr")
    await _drive_queue(harness["engine"], harness["queue"])

    run = harness["run_repo"].get_by_id("run-pr")
    tasks = harness["task_repo"].list_by_run("run-pr")
    inp_tasks = [t for t in tasks if t.node_id == "inp"]
    assert run.status == RunStatus.PENDING, f"run should pend, got {run.status}"
    assert len(inp_tasks) == 1, (
        f"the in-flight request must not be duplicated, got "
        f"{[(t.id, t.status) for t in inp_tasks]}"
    )
    assert "inp" in run.context.pending_revisits, (
        "second arrival during the PENDING window must queue a revisit"
    )
    requests = harness["publisher"].events_of(EventTopic.INPUT_REQUESTED)
    assert len(requests) == 1, f"expected exactly 1 InputRequested, got {len(requests)}"

    # First resume → the deferred revisit drains and re-requests input.
    await _resume_pending(harness, "run-pr", inp_tasks[0])
    await _drive_queue(harness["engine"], harness["queue"])

    run = harness["run_repo"].get_by_id("run-pr")
    assert run.status == RunStatus.PENDING, (
        f"drained revisit should re-request input, got {run.status}: {run.error}"
    )
    inp_tasks = [
        t for t in harness["task_repo"].list_by_run("run-pr")
        if t.node_id == "inp" and t.status == TaskStatus.PENDING
    ]
    assert len(inp_tasks) == 1, "revisit should mint one fresh pending input task"
    requests = harness["publisher"].events_of(EventTopic.INPUT_REQUESTED)
    assert len(requests) == 2, f"expected a 2nd InputRequested, got {len(requests)}"

    # Second resume → run completes.
    await _resume_pending(harness, "run-pr", inp_tasks[0])
    await _drive_queue(harness["engine"], harness["queue"])

    run = harness["run_repo"].get_by_id("run-pr")
    assert run.status == RunStatus.COMPLETED, (
        f"run ended {run.status}: {run.error} — deferred revisit left the run stuck"
    )
    assert not run.context.pending_revisits


# ──────────────────────────────────────────────────────────────────
# 5. Late edge re-entering a satisfied n_of_m MERGE
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_late_arrival_into_satisfied_n_of_m_merge():
    """After an n_of_m MERGE fires on the first two arrivals, a third
    (slow-path) arrival re-enters the satisfied MERGE. Contract: the run
    terminates AND downstream fires at most once — the straggler is absorbed
    by the quorum buffer's fired marker instead of re-running ``final``."""
    workflow = Workflow(
        id="wf-merge-revisit", name="merge-revisit", version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="a", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(id="b", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(id="s1", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(id="s2", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(id="s3", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(
                id="m", node_type=NodeType.MERGE,
                config={"merge_strategy": "n_of_m", "n_required": 2},
            ),
            WorkflowNode(id="final", node_type=NodeType.COMPUTE, config=EXPR),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="a"),
            WorkflowEdge(id="e2", source_node_id="trigger", target_node_id="b"),
            WorkflowEdge(id="e3", source_node_id="trigger", target_node_id="s1"),
            WorkflowEdge(id="e4", source_node_id="a", target_node_id="m"),
            WorkflowEdge(id="e5", source_node_id="b", target_node_id="m"),
            WorkflowEdge(id="e6", source_node_id="s1", target_node_id="s2"),
            WorkflowEdge(id="e7", source_node_id="s2", target_node_id="s3"),
            WorkflowEdge(id="e8", source_node_id="s3", target_node_id="m"),
            WorkflowEdge(id="e9", source_node_id="m", target_node_id="final"),
        ],
    )
    harness = _build_harness(workflow)
    _start_run(harness, workflow, "run-mr")
    await harness["engine"].orchestrate_start("run-mr")
    await _drive_queue(harness["engine"], harness["queue"])

    run = harness["run_repo"].get_by_id("run-mr")
    tasks = harness["task_repo"].list_by_run("run-mr")
    m_tasks = [(t.id, str(t.status)) for t in tasks if t.node_id == "m"]
    final_tasks = [(t.id, str(t.status)) for t in tasks if t.node_id == "final"]
    print(f"\nrun.status={run.status} error={run.error!r}")
    print(f"merge tasks: {m_tasks}")
    print(f"final tasks: {final_tasks}")
    print(f"node_visits={run.context.node_visits}")

    assert run.status == RunStatus.COMPLETED, (
        f"run ended {run.status}: {run.error} — late merge arrival broke liveness"
    )
    final_completed = [t for t in tasks if t.node_id == "final" and t.status == TaskStatus.COMPLETED]
    assert len(final_completed) == 1, (
        f"downstream must fire at most once per quorum; final tasks: {final_tasks}"
    )


# ──────────────────────────────────────────────────────────────────
# 6. Second arrival into a WORKFLOW node whose child is PENDING
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_second_arrival_into_workflow_node_with_pending_child_defers_revisit():
    """Revisit × sub-workflow × pending (SA-846 Mode A, fixed): while the
    child run pends on INPUT, the parent WORKFLOW task stays RUNNING, so a
    second taken edge into ``w`` is recorded as a pending revisit. After the
    first child resumes and completes, the drain re-runs ``w`` — a second
    child run spawns (and pends on its own INPUT in turn)."""
    child = Workflow(
        id="pend-rv-child", name="pend-rv-child", version="1",
        nodes=[
            WorkflowNode(
                id="c-input", node_type=NodeType.INPUT,
                config={
                    "instruction": "give input",
                    "input_schema": [{"key": "name", "type": "string", "label": "Name"}],
                },
            ),
        ],
        edges=[],
    )
    parent = Workflow(
        id="pend-rv-parent", name="pend-rv-parent", version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="a", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(id="b", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(id="c", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(
                id="w", node_type=NodeType.WORKFLOW,
                config={"workflow_id": "pend-rv-child"},
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
    harness = _build_harness(parent, child)
    _start_run(harness, parent, "run-prv")
    await harness["engine"].orchestrate_start("run-prv")
    await _drive_queue(harness["engine"], harness["queue"])

    children = _child_runs_of(harness, "run-prv")
    assert len(children) == 1, (
        f"in-flight window: only the first child should exist yet, got "
        f"{[(r.id, str(r.status)) for r in children]}"
    )
    child_run = children[0]
    assert child_run.status == RunStatus.PENDING
    parent_run = harness["run_repo"].get_by_id("run-prv")
    assert "w" in parent_run.context.pending_revisits, (
        "second arrival during the child-run window must queue a revisit"
    )

    pending = [
        t for t in harness["task_repo"].list_by_run(child_run.id)
        if t.node_id == "c-input" and t.status == TaskStatus.PENDING
    ]
    await _resume_pending(harness, child_run.id, pending[0])
    await _drive_queue(harness["engine"], harness["queue"])

    # First child completed → drain re-ran ``w`` → second child pends.
    children = _child_runs_of(harness, "run-prv")
    assert len(children) == 2, (
        f"deferred revisit should spawn a second child, got "
        f"{[(r.id, str(r.status)) for r in children]}"
    )
    second_child = next(r for r in children if r.id != child_run.id)
    assert second_child.status == RunStatus.PENDING

    pending2 = [
        t for t in harness["task_repo"].list_by_run(second_child.id)
        if t.node_id == "c-input" and t.status == TaskStatus.PENDING
    ]
    await _resume_pending(harness, second_child.id, pending2[0])
    await _drive_queue(harness["engine"], harness["queue"])

    parent_run = harness["run_repo"].get_by_id("run-prv")
    assert parent_run.status == RunStatus.COMPLETED, (
        f"parent ended {parent_run.status}: {parent_run.error}"
    )
    assert not parent_run.context.pending_revisits


# ──────────────────────────────────────────────────────────────────
# 7. Revisited WORKFLOW node whose child itself nests a sub-workflow
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revisited_workflow_node_with_nested_child_spawns_second_tree():
    """Revisit × NESTED sub-workflow (SA-846 Mode A, fixed): the second
    taken edge lands while the nested child tree is still in flight (the
    deeper the child, the wider that window). The deferred revisit drains
    once the first tree completes, so a second child tree — including its
    own grandchild — re-executes. Timing no longer decides whether a
    revisit happens."""
    grandchild = Workflow(
        id="rv-gc", name="rv-gc", version="1",
        nodes=[WorkflowNode(id="gc-compute", node_type=NodeType.COMPUTE, config=EXPR)],
        edges=[],
    )
    child = Workflow(
        id="rv-child", name="rv-child", version="1",
        nodes=[
            WorkflowNode(id="c-compute", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(
                id="c-w", node_type=NodeType.WORKFLOW,
                config={"workflow_id": "rv-gc"},
            ),
        ],
        edges=[WorkflowEdge(id="ce1", source_node_id="c-compute", target_node_id="c-w")],
    )
    parent = Workflow(
        id="rv-parent", name="rv-parent", version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="a", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(id="b", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(id="c", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(id="d", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(id="e", node_type=NodeType.COMPUTE, config=EXPR),
            WorkflowNode(
                id="w", node_type=NodeType.WORKFLOW,
                config={"workflow_id": "rv-child"},
            ),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="a"),
            WorkflowEdge(id="e2", source_node_id="trigger", target_node_id="b"),
            WorkflowEdge(id="e3", source_node_id="a", target_node_id="w"),
            WorkflowEdge(id="e4", source_node_id="b", target_node_id="c"),
            WorkflowEdge(id="e5", source_node_id="c", target_node_id="d"),
            WorkflowEdge(id="e6", source_node_id="d", target_node_id="e"),
            WorkflowEdge(id="e7", source_node_id="e", target_node_id="w"),
        ],
    )
    harness = _build_harness(parent, child, grandchild)
    _start_run(harness, parent, "run-rvn")
    await harness["engine"].orchestrate_start("run-rvn")
    await _drive_queue(harness["engine"], harness["queue"])

    run = harness["run_repo"].get_by_id("run-rvn")
    children = _child_runs_of(harness, "run-rvn")
    print(f"\nrun.status={run.status} error={run.error!r}")
    print(f"children: {[(r.id, str(r.status)) for r in children]}")

    assert run.status == RunStatus.COMPLETED, f"run ended {run.status}: {run.error}"
    assert len(children) == 2, (
        f"deferred revisit should spawn a second child tree, got "
        f"{[(r.id, str(r.status)) for r in children]}"
    )
    for child_run in children:
        assert child_run.status == RunStatus.COMPLETED
        gcs = _child_runs_of(harness, child_run.id)
        assert len(gcs) == 1 and gcs[0].status == RunStatus.COMPLETED, (
            f"nested grandchild tree broken for {child_run.id}: "
            f"{[(g.id, str(g.status)) for g in gcs]}"
        )
    assert not run.context.pending_revisits


# ──────────────────────────────────────────────────────────────────
# 8. Child workflows follow the publish-version rule
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_child_workflow_resolves_latest_published_version():
    """Governance parity with POST /runs: a WORKFLOW node referencing a
    logical child id must execute the latest PUBLISHED version even when a
    newer DRAFT exists (and even when no row matches the raw id — the old
    ``aget_by_id`` resolution failed this graph outright)."""
    from app.layer1_domain.value_objects.workflow_status import WorkflowStatus

    child_v1 = Workflow(
        id="vc-child-v1", workflow_id="vc-child", version="1",
        name="published", status=WorkflowStatus.PUBLISHED,
        updated_at="2026-01-01T00:00:00",
        nodes=[WorkflowNode(id="c1", node_type=NodeType.COMPUTE, config=EXPR)],
        edges=[],
    )
    child_v2_draft = Workflow(
        id="vc-child-v2", workflow_id="vc-child", version="2",
        name="draft", status=WorkflowStatus.DRAFT,
        updated_at="2026-02-01T00:00:00",
        nodes=[WorkflowNode(id="c1", node_type=NodeType.COMPUTE, config=EXPR)],
        edges=[],
    )
    parent = Workflow(
        id="vc-parent", name="vc-parent", version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="w", node_type=NodeType.WORKFLOW,
                config={"workflow_id": "vc-child"},
            ),
        ],
        edges=[WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="w")],
    )
    harness = _build_harness(parent, child_v1, child_v2_draft)
    _start_run(harness, parent, "run-vc")
    await harness["engine"].orchestrate_start("run-vc")
    await _drive_queue(harness["engine"], harness["queue"])

    run = harness["run_repo"].get_by_id("run-vc")
    children = _child_runs_of(harness, "run-vc")
    assert run.status == RunStatus.COMPLETED, f"run ended {run.status}: {run.error}"
    assert len(children) == 1
    child_run = children[0]
    assert child_run.status == RunStatus.COMPLETED
    # The child ran the PUBLISHED v1, not the newer draft.
    assert child_run.workflow_version == "1", (
        f"child should execute the latest PUBLISHED version, got "
        f"v{child_run.workflow_version}"
    )
