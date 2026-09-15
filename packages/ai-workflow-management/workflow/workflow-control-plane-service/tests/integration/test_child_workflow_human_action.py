"""Integration tests for HUMAN_ACTION as a child workflow entry node.

These tests exercise the *production* data path:
- ``DataStoreService`` wraps node outputs as file references (CSV-backed).
- ``FileBackedResolverStrategy`` reads back values via the file service.
- ``MockFileServiceClient`` stands in for the real file service.

They reproduce the user-reported scenario: a parent workflow whose
``WORKFLOW`` node calls a child whose entry node is ``HUMAN_ACTION``.
The child should pause, then advance after ``ResumePendingTaskUseCase``
delivers the user's action, and the parent should subsequently advance
past the ``WORKFLOW`` node.

Both the synchronous fallback path and the queued (broker-style) path
are covered, and both ``confirm`` and ``reject`` actions are exercised.
"""

import asyncio

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus

from app.layer2_application.resolve.variable_resolver import (
    VariableResolver,
    ConditionEvaluator,
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
from app.layer2_application.node_handlers.human_action_handler import HumanActionNodeHandler
from app.layer2_application.node_handlers.condition_handler import ConditionNodeHandler

from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import MemoryStageHistoryRepository
from app.layer2_application.orchestration_queue.memory_queue import MemoryOrchestrationQueue

from app.layer2_application.interfaces.orchestration_queue_interface import (
    ResumeTaskAction,
)
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunUseCase, StartRunCommand,
)
from app.layer2_application.features.resume_pending_task.use_cases.resume_pending_task_use_case import (
    ResumePendingTaskUseCase, ResumePendingTaskCommand,
)
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskUseCase, CompleteTaskCommand,
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


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class DummyLogger:
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


class CapturingEventPublisher:
    """Captures all published (topic, event) tuples for assertion."""

    def __init__(self):
        self.events: list[tuple[str, object]] = []

    def publish(self, topic, event):
        self.events.append((topic, event))

    def topics(self) -> list[str]:
        return [t for t, _ in self.events]


# ---------------------------------------------------------------------------
# Workflow fixtures
# ---------------------------------------------------------------------------

def _parent_workflow_calls_child() -> Workflow:
    """TRIGGER -> WORKFLOW(child) -> TASK(final)."""
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
            WorkflowNode(id="final-task", name="FinalTask", node_type=NodeType.TASK, worker_type="w-final"),
        ],
        edges=[
            WorkflowEdge(source_node_id="trigger", target_node_id="wf-node"),
            WorkflowEdge(source_node_id="wf-node", target_node_id="final-task"),
        ],
    )


def _parent_workflow_calls_child_as_leaf() -> Workflow:
    """TRIGGER -> WORKFLOW(child)  (WORKFLOW is the parent's leaf).

    When the child completes, there are no further parent successors, so
    ``check_run_completion`` should transition the parent to COMPLETED.
    """
    return Workflow(
        id="parent-wf",
        name="ParentLeafChild",
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


def _child_workflow_human_action_only() -> Workflow:
    """Single HUMAN_ACTION node (entry + leaf)."""
    return Workflow(
        id="child-wf",
        name="ChildHA",
        nodes=[
            WorkflowNode(
                id="child-ha",
                name="ChildHumanAction",
                node_type=NodeType.HUMAN_ACTION,
                config={
                    "instruction": "Approve the child step",
                    "input_schema": [],
                    "confirm_label": "Approve",
                    "reject_label": "Reject",
                },
            ),
        ],
        edges=[],
    )


def _child_workflow_human_action_then_task() -> Workflow:
    """HUMAN_ACTION (entry, branched) -> TASK on confirm port."""
    return Workflow(
        id="child-wf",
        name="ChildHA_then_Task",
        nodes=[
            WorkflowNode(
                id="child-ha",
                name="ChildHumanAction",
                node_type=NodeType.HUMAN_ACTION,
                config={
                    "instruction": "Approve the child step",
                    "input_schema": [],
                    "confirm_label": "Approve",
                    "reject_label": "Reject",
                },
            ),
            WorkflowNode(
                id="child-task",
                name="ChildTask",
                node_type=NodeType.TASK,
                worker_type="w-child",
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="child-ha",
                target_node_id="child-task",
                source_port_id="confirm",
            ),
        ],
    )


def _ha_node(node_id: str = "child-ha", name: str = "ChildHumanAction") -> WorkflowNode:
    return WorkflowNode(
        id=node_id,
        name=name,
        node_type=NodeType.HUMAN_ACTION,
        config={
            "instruction": "Approve the step",
            "input_schema": [],
            "confirm_label": "Approve",
            "reject_label": "Reject",
        },
    )


def _child_workflow_task_before_ha() -> Workflow:
    """Child: TASK -> HUMAN_ACTION (HA is the leaf).

    Tests that the HA flow works when there's an upstream TASK that must
    complete first.
    """
    return Workflow(
        id="child-wf",
        name="ChildTaskBeforeHA",
        nodes=[
            WorkflowNode(
                id="child-pre-task",
                name="ChildPreTask",
                node_type=NodeType.TASK,
                worker_type="w-pre",
            ),
            _ha_node(),
        ],
        edges=[
            WorkflowEdge(source_node_id="child-pre-task", target_node_id="child-ha"),
        ],
    )


def _child_workflow_task_after_ha_unconditional() -> Workflow:
    """Child: HUMAN_ACTION -> TASK (unconditional edge, no port routing).

    Both confirm and reject should dispatch the downstream TASK because
    the edge has no ``source_port_id``.
    """
    return Workflow(
        id="child-wf",
        name="ChildTaskAfterHA",
        nodes=[
            _ha_node(),
            WorkflowNode(
                id="child-post-task",
                name="ChildPostTask",
                node_type=NodeType.TASK,
                worker_type="w-post",
            ),
        ],
        edges=[
            WorkflowEdge(source_node_id="child-ha", target_node_id="child-post-task"),
        ],
    )


def _child_workflow_condition_before_ha() -> Workflow:
    """Child: CONDITION (always-true) -> HUMAN_ACTION (HA is the leaf).

    The condition's true branch goes to HA; the false branch goes nowhere
    so an always-true condition exercises the HA path.
    """
    return Workflow(
        id="child-wf",
        name="ChildConditionBeforeHA",
        nodes=[
            WorkflowNode(
                id="child-cond",
                name="ChildCondition",
                node_type=NodeType.CONDITION,
                config={"expression": "1 == 1"},
            ),
            _ha_node(),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="child-cond",
                target_node_id="child-ha",
                source_port_id="true_branch",
            ),
        ],
    )


def _child_workflow_condition_after_ha() -> Workflow:
    """Child: HUMAN_ACTION -> CONDITION -> (then) TASK_then / (else) TASK_else.

    The CONDITION branches on the user's action: ``confirm`` routes to
    TASK_then, ``reject`` routes to TASK_else. Both branches are leaf
    tasks so the child completes once the dispatched task is finished.
    """
    return Workflow(
        id="child-wf",
        name="ChildConditionAfterHA",
        nodes=[
            _ha_node(),
            WorkflowNode(
                id="child-cond",
                name="ChildCondition",
                node_type=NodeType.CONDITION,
                config={"expression": '{{$child-ha._action}} == "confirm"'},
            ),
            WorkflowNode(
                id="child-then-task",
                name="ChildThenTask",
                node_type=NodeType.TASK,
                worker_type="w-then",
            ),
            WorkflowNode(
                id="child-else-task",
                name="ChildElseTask",
                node_type=NodeType.TASK,
                worker_type="w-else",
            ),
        ],
        edges=[
            WorkflowEdge(source_node_id="child-ha", target_node_id="child-cond"),
            WorkflowEdge(
                source_node_id="child-cond",
                target_node_id="child-then-task",
                source_port_id="true_branch",
            ),
            WorkflowEdge(
                source_node_id="child-cond",
                target_node_id="child-else-task",
                source_port_id="false_branch",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# DI fixture: full production-like dependency wiring
# ---------------------------------------------------------------------------

@pytest.fixture
def deps():
    """Wire DataStoreService + FileBackedResolverStrategy + all handlers
    needed for child-workflow-with-HUMAN_ACTION scenarios.
    """
    file_client = MockFileServiceClient()
    csv_converter = CsvConverter()
    data_store = DataStoreService(file_client, csv_converter)

    resolver = VariableResolver(strategies=[
        FileBackedResolverStrategy(data_store),
        MetadataResolverStrategy(),
        TraversalResolverStrategy(),
    ])
    condition_evaluator = ConditionEvaluator(resolver)

    registry = NodeHandlerRegistry()
    registry.register(NodeType.TRIGGER, TriggerNodeHandler())
    registry.register(NodeType.TASK, TaskNodeHandler(resolver))
    registry.register(NodeType.WORKFLOW, WorkflowNodeHandler(resolver))
    registry.register(NodeType.HUMAN_ACTION, HumanActionNodeHandler(resolver))
    registry.register(NodeType.CONDITION, ConditionNodeHandler(condition_evaluator))

    task_factory = TaskFactory()
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    workflow_repo = MemoryWorkflowRepository()
    stage_history_repo = MemoryStageHistoryRepository()
    stage_recorder = StageRecorder(stage_history_repo)

    event_publisher = CapturingEventPublisher()
    event_log_repo = MemoryEventLogRepository()
    # Production chain shape: events hit the append-only log BEFORE any
    # broadcast; the projector view store reads the log. Node outputs are
    # view-only (Phase 3b.1), so handlers resolving ``{{$node.field}}``
    # need this chain — the bare capturing publisher left
    # ``run.context.variables`` without node outputs entirely.
    logging_publisher = EventLogAppendingPublisher(
        inner=event_publisher,
        event_log_repo=event_log_repo,
        logger=DummyLogger(),
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
        "logger": DummyLogger(),
        "queue": queue,
        "view_store": view_store,
        "workflow_repo": workflow_repo,
        "run_repo": run_repo,
        "task_repo": task_repo,
        "stage_recorder": stage_recorder,
        "orchestration_engine": engine,
        "event_publisher": event_publisher,
        "variable_resolver": resolver,
        "data_store_service": data_store,
        "file_client": file_client,
    }


def _make_start_run_use_case(deps, queue=None) -> StartRunUseCase:
    return StartRunUseCase(
        logger=deps["logger"],
        workflow_repo=deps["workflow_repo"],
        run_repo=deps["run_repo"],
        task_repo=deps["task_repo"],
        orchestration_engine=deps["orchestration_engine"],
        stage_recorder=deps["stage_recorder"],
        event_publisher=deps["event_publisher"],
        data_store_service=deps["data_store_service"],
        orchestration_queue=queue,
    )


def _make_complete_task_use_case(deps, queue=None) -> CompleteTaskUseCase:
    return CompleteTaskUseCase(
        logger=deps["logger"],
        workflow_repo=deps["workflow_repo"],
        run_repo=deps["run_repo"],
        task_repo=deps["task_repo"],
        orchestration_engine=deps["orchestration_engine"],
        stage_recorder=deps["stage_recorder"],
        variable_resolver=deps["variable_resolver"],
        data_store_service=deps["data_store_service"],
        orchestration_queue=queue,
    )


def _make_resume_use_case(deps, queue=None) -> ResumePendingTaskUseCase:
    return ResumePendingTaskUseCase(
        logger=deps["logger"],
        run_repo=deps["run_repo"],
        task_repo=deps["task_repo"],
        workflow_repo=deps["workflow_repo"],
        stage_recorder=deps["stage_recorder"],
        event_publisher=deps["event_publisher"],
        orchestration_engine=deps["orchestration_engine"],
        data_store_service=deps["data_store_service"],
        file_service_client=deps["file_client"],
        orchestration_queue=queue,
    )


async def _drain_queue(deps, *, max_steps: int = 300) -> int:
    """Drive the production ``OrchestrationWorker`` over the engine's queue
    until it quiesces.

    Every use case (StartRun / CompleteTask / ResumePendingTask) now
    enqueues orchestration actions instead of orchestrating inline, so
    each use-case call in these tests must be followed by a drain —
    exactly what the worker loop does in production, minus run_forever.
    """
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
    """Start the parent run via StartRunUseCase and drain the queue."""
    start_uc = _make_start_run_use_case(deps)
    result = await start_uc.execute(StartRunCommand(
        workflow_id="parent-wf",
        input_data={},
    ))
    assert (result.error or "") == "", f"Parent start failed: {result.error}"
    await _drain_queue(deps)
    return result.id


def _get_child_state(deps, parent_run_id: str) -> tuple[str, str]:
    """Return (child_run_id, child_ha_task_id) and assert it's PENDING."""
    parent_run = deps["run_repo"].get_by_id(parent_run_id)
    child_run_id = parent_run.context.child_runs.get("wf-node")
    assert child_run_id, f"No child_runs entry for wf-node; parent ctx: {parent_run.context.child_runs}"

    child_run = deps["run_repo"].get_by_id(child_run_id)
    assert child_run.status == RunStatus.PENDING, (
        f"Child run should be PENDING after start (HA pauses); got {child_run.status}"
    )

    child_tasks = deps["task_repo"].list_by_run(child_run_id)
    ha_tasks = [t for t in child_tasks if t.node_id == "child-ha"]
    assert len(ha_tasks) == 1, (
        f"Expected 1 child-ha task, got {len(ha_tasks)}: {[(t.node_id, t.status) for t in child_tasks]}"
    )
    assert ha_tasks[0].status == TaskStatus.PENDING

    return child_run_id, ha_tasks[0].id


# ---------------------------------------------------------------------------
# Tests: child-only HUMAN_ACTION (HA is leaf)
# ---------------------------------------------------------------------------

class TestChildWorkflowHumanActionOnlyEntry:
    """HUMAN_ACTION is the child's only node — entry AND leaf."""

    @pytest.mark.asyncio
    async def test_parent_starts_child_and_pauses_on_human_action(self, deps):
        """Sanity check: parent.start() creates child run, child pauses on HA."""
        deps["workflow_repo"].save(_parent_workflow_calls_child())
        deps["workflow_repo"].save(_child_workflow_human_action_only())

        parent_run_id = await _start_parent_run(deps)
        child_run_id, child_ha_task_id = _get_child_state(deps, parent_run_id)

        # Parent should still be RUNNING — its WORKFLOW node is waiting on the child.
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert parent_run.status == RunStatus.RUNNING
        assert "wf-node" not in parent_run.context.completed_nodes

        # Final task should NOT have been dispatched yet.
        final_tasks = [
            t for t in deps["task_repo"].list_by_run(parent_run_id)
            if t.node_id == "final-task"
        ]
        assert final_tasks == []

    @pytest.mark.asyncio
    async def test_resume_with_confirm_completes_child_and_advances_parent(self, deps):
        """Confirm action: child completes, parent advances to final-task."""
        deps["workflow_repo"].save(_parent_workflow_calls_child())
        deps["workflow_repo"].save(_child_workflow_human_action_only())

        parent_run_id = await _start_parent_run(deps)
        child_run_id, child_ha_task_id = _get_child_state(deps, parent_run_id)

        resume_uc = _make_resume_use_case(deps)
        result = await resume_uc.execute(ResumePendingTaskCommand(
            run_id=child_run_id,
            task_id=child_ha_task_id,
            updated_input={"_action": "confirm"},
        ))
        assert result.resumed is True
        await _drain_queue(deps)

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child run should be COMPLETED; got {child_run.status}, "
            f"completed_nodes={child_run.context.completed_nodes}, "
            f"skipped_nodes={child_run.context.skipped_nodes}"
        )

        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert "wf-node" in parent_run.context.completed_nodes, (
            f"Parent should advance past WORKFLOW; "
            f"completed_nodes={parent_run.context.completed_nodes}, "
            f"skipped_nodes={parent_run.context.skipped_nodes}"
        )

        final_tasks = [
            t for t in deps["task_repo"].list_by_run(parent_run_id)
            if t.node_id == "final-task"
        ]
        assert len(final_tasks) == 1
        assert final_tasks[0].status == TaskStatus.RUNNING

    @pytest.mark.asyncio
    async def test_resume_with_reject_completes_child_and_advances_parent(self, deps):
        """Reject action: child has no successor edges, so child still completes
        and parent advances. This guards against the file-ref / _action mismatch
        in `_get_human_action_branch` re-deriving the wrong branch on the worker
        path.
        """
        deps["workflow_repo"].save(_parent_workflow_calls_child())
        deps["workflow_repo"].save(_child_workflow_human_action_only())

        parent_run_id = await _start_parent_run(deps)
        child_run_id, child_ha_task_id = _get_child_state(deps, parent_run_id)

        resume_uc = _make_resume_use_case(deps)
        result = await resume_uc.execute(ResumePendingTaskCommand(
            run_id=child_run_id,
            task_id=child_ha_task_id,
            updated_input={"_action": "reject"},
        ))
        assert result.resumed is True
        await _drain_queue(deps)

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child run should be COMPLETED on reject (HA is leaf); got {child_run.status}, "
            f"completed_nodes={child_run.context.completed_nodes}, "
            f"skipped_nodes={child_run.context.skipped_nodes}"
        )

        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert "wf-node" in parent_run.context.completed_nodes, (
            f"Parent should advance after child completed (reject); "
            f"completed_nodes={parent_run.context.completed_nodes}"
        )


# ---------------------------------------------------------------------------
# Tests: child HUMAN_ACTION -> TASK (confirm-port edge)
# ---------------------------------------------------------------------------

class TestChildWorkflowHumanActionThenTask:
    """HUMAN_ACTION -> TASK with explicit ``confirm`` port edge."""

    @pytest.mark.asyncio
    async def test_resume_with_confirm_dispatches_child_task(self, deps):
        deps["workflow_repo"].save(_parent_workflow_calls_child())
        deps["workflow_repo"].save(_child_workflow_human_action_then_task())

        parent_run_id = await _start_parent_run(deps)
        child_run_id, child_ha_task_id = _get_child_state(deps, parent_run_id)

        resume_uc = _make_resume_use_case(deps)
        result = await resume_uc.execute(ResumePendingTaskCommand(
            run_id=child_run_id,
            task_id=child_ha_task_id,
            updated_input={"_action": "confirm"},
        ))
        assert result.resumed is True
        await _drain_queue(deps)

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert "child-ha" in child_run.context.completed_nodes
        assert "child-task" not in child_run.context.skipped_nodes, (
            f"child-task should NOT be skipped on confirm; "
            f"skipped_nodes={child_run.context.skipped_nodes}"
        )

        child_tasks = deps["task_repo"].list_by_run(child_run_id)
        child_task = [t for t in child_tasks if t.node_id == "child-task"]
        assert len(child_task) == 1, (
            f"child-task was not dispatched after confirm; "
            f"tasks={[(t.node_id, t.status) for t in child_tasks]}"
        )
        assert child_task[0].status == TaskStatus.RUNNING

        # Child still RUNNING (waiting on child-task), parent still waiting.
        assert child_run.status == RunStatus.RUNNING
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert "wf-node" not in parent_run.context.completed_nodes

    @pytest.mark.asyncio
    async def test_resume_with_reject_skips_child_task_and_completes_child(self, deps):
        """Reject when only a confirm-port edge exists: the confirm successor
        must be skipped and the child run should complete (not block).

        This is the high-risk scenario. After ``ResumePendingTaskUseCase``
        stores the HA output via ``DataStoreService``, the value at
        ``run.context.variables['child-ha']`` is a *file_ref* dict — it does
        NOT have ``_action`` at the top level.

        If a downstream call site re-derives the branch via
        ``successor_processor._get_human_action_branch`` (which reads
        ``_action`` from the variable directly, not via the resolver),
        it would default to ``confirm`` and try to re-dispatch the
        confirm-port target — leaving the child stuck.
        """
        deps["workflow_repo"].save(_parent_workflow_calls_child())
        deps["workflow_repo"].save(_child_workflow_human_action_then_task())

        parent_run_id = await _start_parent_run(deps)
        child_run_id, child_ha_task_id = _get_child_state(deps, parent_run_id)

        resume_uc = _make_resume_use_case(deps)
        result = await resume_uc.execute(ResumePendingTaskCommand(
            run_id=child_run_id,
            task_id=child_ha_task_id,
            updated_input={"_action": "reject"},
        ))
        assert result.resumed is True
        await _drain_queue(deps)

        child_run = deps["run_repo"].get_by_id(child_run_id)
        # SA-846 edge-based dispatch: the untaken confirm-port successor is
        # simply never run — no skip bookkeeping (cascade-skip retired).
        assert "child-task" not in child_run.context.completed_nodes, (
            f"child-task must not run on reject (only edge is confirm port); "
            f"completed_nodes={child_run.context.completed_nodes}"
        )
        # child-task should NOT have been dispatched.
        child_tasks = deps["task_repo"].list_by_run(child_run_id)
        dispatched_child_tasks = [
            t for t in child_tasks
            if t.node_id == "child-task" and t.status == TaskStatus.RUNNING
        ]
        assert dispatched_child_tasks == [], (
            f"child-task was dispatched on reject; tasks="
            f"{[(t.node_id, t.status) for t in child_tasks]}"
        )

        # Child must complete (no remaining live work).
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child should be COMPLETED on reject; got {child_run.status}, "
            f"completed_nodes={child_run.context.completed_nodes}, "
            f"skipped_nodes={child_run.context.skipped_nodes}"
        )

        # Parent must advance.
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert "wf-node" in parent_run.context.completed_nodes, (
            f"Parent should advance after child rejected; "
            f"completed_nodes={parent_run.context.completed_nodes}"
        )


# ---------------------------------------------------------------------------
# Tests: queued (broker-style) path — mirrors production multi-instance flow
# ---------------------------------------------------------------------------

class TestChildWorkflowHumanActionQueuedPath:
    """When ``orchestration_queue`` is wired in, ``ResumePendingTaskUseCase``
    enqueues a ``ResumeTaskAction`` and the orchestration worker drains the
    queue.  This test mirrors that path with ``MemoryOrchestrationQueue``
    plus a manual dequeue + ``orchestrate_resume`` call.
    """

    @pytest.mark.asyncio
    async def test_queued_resume_with_confirm_completes_child(self, deps):
        deps["workflow_repo"].save(_parent_workflow_calls_child())
        deps["workflow_repo"].save(_child_workflow_human_action_only())

        parent_run_id = await _start_parent_run(deps)
        child_run_id, child_ha_task_id = _get_child_state(deps, parent_run_id)

        queue = MemoryOrchestrationQueue()
        resume_uc = _make_resume_use_case(deps, queue=queue)

        result = await resume_uc.execute(ResumePendingTaskCommand(
            run_id=child_run_id,
            task_id=child_ha_task_id,
            updated_input={"_action": "confirm"},
        ))
        assert result.resumed is True
        await _drain_queue(deps)

        # The use case enqueued a ResumeTaskAction. Dequeue + dispatch.
        action = await queue.dequeue()
        assert isinstance(action, ResumeTaskAction)
        assert action.run_id == child_run_id
        assert action.task_id == child_ha_task_id
        await deps["orchestration_engine"].orchestrate_resume(
            action.run_id, action.task_id, test_mode=action.test_mode,
        )
        await _drain_queue(deps)

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child run should be COMPLETED via queue path; got {child_run.status}, "
            f"completed_nodes={child_run.context.completed_nodes}, "
            f"skipped_nodes={child_run.context.skipped_nodes}"
        )

        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert "wf-node" in parent_run.context.completed_nodes

    @pytest.mark.asyncio
    async def test_queued_resume_with_reject_completes_child(self, deps):
        """Queued path + reject: most likely to expose the file_ref vs
        ``_action`` mismatch, because ``orchestrate_resume`` (= ``orchestrate_continue``)
        re-runs ``successor_processor.process``, which calls
        ``_get_human_action_branch`` on the now-file_ref-shaped variable.
        """
        deps["workflow_repo"].save(_parent_workflow_calls_child())
        deps["workflow_repo"].save(_child_workflow_human_action_only())

        parent_run_id = await _start_parent_run(deps)
        child_run_id, child_ha_task_id = _get_child_state(deps, parent_run_id)

        queue = MemoryOrchestrationQueue()
        resume_uc = _make_resume_use_case(deps, queue=queue)

        result = await resume_uc.execute(ResumePendingTaskCommand(
            run_id=child_run_id,
            task_id=child_ha_task_id,
            updated_input={"_action": "reject"},
        ))
        assert result.resumed is True
        await _drain_queue(deps)

        action = await queue.dequeue()
        assert isinstance(action, ResumeTaskAction)
        await deps["orchestration_engine"].orchestrate_resume(
            action.run_id, action.task_id, test_mode=action.test_mode,
        )
        await _drain_queue(deps)

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child run should be COMPLETED via queue path on reject; got {child_run.status}, "
            f"completed_nodes={child_run.context.completed_nodes}, "
            f"skipped_nodes={child_run.context.skipped_nodes}"
        )

        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert "wf-node" in parent_run.context.completed_nodes

    @pytest.mark.asyncio
    async def test_queued_resume_reject_with_confirm_only_edge_does_not_block_child(self, deps):
        """The exact production-shaped scenario: HA -> TASK on confirm port,
        user rejects, queued worker path — should mark child-task skipped
        and complete the child rather than leave it RUNNING with no live work.
        """
        deps["workflow_repo"].save(_parent_workflow_calls_child())
        deps["workflow_repo"].save(_child_workflow_human_action_then_task())

        parent_run_id = await _start_parent_run(deps)
        child_run_id, child_ha_task_id = _get_child_state(deps, parent_run_id)

        queue = MemoryOrchestrationQueue()
        resume_uc = _make_resume_use_case(deps, queue=queue)

        result = await resume_uc.execute(ResumePendingTaskCommand(
            run_id=child_run_id,
            task_id=child_ha_task_id,
            updated_input={"_action": "reject"},
        ))
        assert result.resumed is True
        await _drain_queue(deps)

        action = await queue.dequeue()
        assert isinstance(action, ResumeTaskAction)
        await deps["orchestration_engine"].orchestrate_resume(
            action.run_id, action.task_id, test_mode=action.test_mode,
        )
        await _drain_queue(deps)

        child_run = deps["run_repo"].get_by_id(child_run_id)

        # SA-846 edge-based dispatch: the untaken confirm-port successor is
        # never run on reject — no skip bookkeeping (cascade-skip retired).
        assert "child-task" not in child_run.context.completed_nodes, (
            f"child-task must not run on reject (queue path); "
            f"completed_nodes={child_run.context.completed_nodes}"
        )

        # No RUNNING child-task should exist.
        child_tasks = deps["task_repo"].list_by_run(child_run_id)
        running_child_tasks = [
            t for t in child_tasks
            if t.node_id == "child-task" and t.status == TaskStatus.RUNNING
        ]
        assert running_child_tasks == [], (
            f"child-task was dispatched on reject via queue path; "
            f"tasks={[(t.node_id, t.status) for t in child_tasks]}"
        )

        # Child should complete (not stuck RUNNING with no live work).
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child should be COMPLETED on queued reject; got {child_run.status}, "
            f"completed_nodes={child_run.context.completed_nodes}, "
            f"skipped_nodes={child_run.context.skipped_nodes}"
        )

        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert "wf-node" in parent_run.context.completed_nodes


# ---------------------------------------------------------------------------
# Tests: parent run also COMPLETES when child completes (WORKFLOW = parent leaf)
# ---------------------------------------------------------------------------

class TestChildWorkflowHumanActionEndToEndCompletion:
    """End-to-end: parent's WORKFLOW node is the parent leaf, child has only
    a HUMAN_ACTION.  After the user confirms or rejects, the child run must
    COMPLETE and the parent run must also COMPLETE (no further work).
    """

    def _start_helper(self, deps):
        deps["workflow_repo"].save(_parent_workflow_calls_child_as_leaf())
        deps["workflow_repo"].save(_child_workflow_human_action_only())

    @pytest.mark.asyncio
    async def test_confirm_completes_child_and_parent_run(self, deps):
        self._start_helper(deps)

        parent_run_id = await _start_parent_run(deps)
        child_run_id, child_ha_task_id = _get_child_state(deps, parent_run_id)

        resume_uc = _make_resume_use_case(deps)
        result = await resume_uc.execute(ResumePendingTaskCommand(
            run_id=child_run_id,
            task_id=child_ha_task_id,
            updated_input={"_action": "confirm"},
        ))
        assert result.resumed is True
        await _drain_queue(deps)

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child must be COMPLETED on confirm; got {child_run.status}, "
            f"completed_nodes={child_run.context.completed_nodes}, "
            f"skipped_nodes={child_run.context.skipped_nodes}"
        )

        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert parent_run.status == RunStatus.COMPLETED, (
            f"Parent must be COMPLETED after child completes (WORKFLOW is leaf); "
            f"got {parent_run.status}, "
            f"completed_nodes={parent_run.context.completed_nodes}, "
            f"skipped_nodes={parent_run.context.skipped_nodes}"
        )

    @pytest.mark.asyncio
    async def test_reject_completes_child_and_parent_run(self, deps):
        self._start_helper(deps)

        parent_run_id = await _start_parent_run(deps)
        child_run_id, child_ha_task_id = _get_child_state(deps, parent_run_id)

        resume_uc = _make_resume_use_case(deps)
        result = await resume_uc.execute(ResumePendingTaskCommand(
            run_id=child_run_id,
            task_id=child_ha_task_id,
            updated_input={"_action": "reject"},
        ))
        assert result.resumed is True
        await _drain_queue(deps)

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child must be COMPLETED on reject; got {child_run.status}, "
            f"completed_nodes={child_run.context.completed_nodes}, "
            f"skipped_nodes={child_run.context.skipped_nodes}"
        )

        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert parent_run.status == RunStatus.COMPLETED, (
            f"Parent must be COMPLETED after child completes on reject; "
            f"got {parent_run.status}, "
            f"completed_nodes={parent_run.context.completed_nodes}, "
            f"skipped_nodes={parent_run.context.skipped_nodes}"
        )

    @pytest.mark.asyncio
    async def test_queued_confirm_completes_child_and_parent_run(self, deps):
        """Production-shaped path: queued worker drains ResumeTaskAction.
        Confirm should still cascade to parent COMPLETED.
        """
        self._start_helper(deps)

        parent_run_id = await _start_parent_run(deps)
        child_run_id, child_ha_task_id = _get_child_state(deps, parent_run_id)

        queue = MemoryOrchestrationQueue()
        resume_uc = _make_resume_use_case(deps, queue=queue)

        result = await resume_uc.execute(ResumePendingTaskCommand(
            run_id=child_run_id,
            task_id=child_ha_task_id,
            updated_input={"_action": "confirm"},
        ))
        assert result.resumed is True
        await _drain_queue(deps)

        action = await queue.dequeue()
        assert isinstance(action, ResumeTaskAction)
        await deps["orchestration_engine"].orchestrate_resume(
            action.run_id, action.task_id, test_mode=action.test_mode,
        )
        await _drain_queue(deps)

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child must be COMPLETED via queued confirm; got {child_run.status}"
        )

        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert parent_run.status == RunStatus.COMPLETED, (
            f"Parent must be COMPLETED after queued child completion (confirm); "
            f"got {parent_run.status}, "
            f"completed_nodes={parent_run.context.completed_nodes}"
        )

    @pytest.mark.asyncio
    async def test_queued_reject_completes_child_and_parent_run(self, deps):
        """Production-shaped path: queued worker drains ResumeTaskAction.
        Reject should still cascade to parent COMPLETED (no successor edges
        on the child HA, so reject behaves like confirm here — child has
        nothing more to do, parent has nothing after WORKFLOW node).
        """
        self._start_helper(deps)

        parent_run_id = await _start_parent_run(deps)
        child_run_id, child_ha_task_id = _get_child_state(deps, parent_run_id)

        queue = MemoryOrchestrationQueue()
        resume_uc = _make_resume_use_case(deps, queue=queue)

        result = await resume_uc.execute(ResumePendingTaskCommand(
            run_id=child_run_id,
            task_id=child_ha_task_id,
            updated_input={"_action": "reject"},
        ))
        assert result.resumed is True
        await _drain_queue(deps)

        action = await queue.dequeue()
        assert isinstance(action, ResumeTaskAction)
        await deps["orchestration_engine"].orchestrate_resume(
            action.run_id, action.task_id, test_mode=action.test_mode,
        )
        await _drain_queue(deps)

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child must be COMPLETED via queued reject; got {child_run.status}"
        )

        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert parent_run.status == RunStatus.COMPLETED, (
            f"Parent must be COMPLETED after queued child completion (reject); "
            f"got {parent_run.status}, "
            f"completed_nodes={parent_run.context.completed_nodes}"
        )


# ---------------------------------------------------------------------------
# Tests: child workflow shapes around HUMAN_ACTION (TASK / CONDITION before+after)
# ---------------------------------------------------------------------------

def _find_running_task(deps, run_id: str, node_id: str):
    """Return the (single) RUNNING task for the given node, or None."""
    matches = [
        t for t in deps["task_repo"].list_by_run(run_id)
        if t.node_id == node_id and t.status == TaskStatus.RUNNING
    ]
    if not matches:
        return None
    assert len(matches) == 1, (
        f"Expected exactly one RUNNING task for node {node_id}, got {len(matches)}"
    )
    return matches[0]


async def _complete_task(deps, run_id: str, task_id: str, output_metadata=None):
    uc = _make_complete_task_use_case(deps)
    result = await uc.execute(CompleteTaskCommand(
        run_id=run_id,
        task_id=task_id,
        status="COMPLETED",
        output_reference="",
        output_metadata=output_metadata or {},
    ))
    assert result.error == "", f"Complete task failed: {result.error}"
    await _drain_queue(deps)
    return result


class TestChildWorkflowHumanActionWithSurroundingNodes:
    """Verify HA flow inside child workflows with TASK/CONDITION before/after.

    Parent shape: TRIGGER -> WORKFLOW (WORKFLOW is parent leaf).
    Each child shape places nodes around the HUMAN_ACTION and we assert
    that on confirm AND on reject, both child and parent reach COMPLETED.
    """

    def _save_parent(self, deps):
        deps["workflow_repo"].save(_parent_workflow_calls_child_as_leaf())

    async def _drive_child_to_ha_pending(self, deps) -> tuple[str, str, str]:
        """Start parent run, wait for HA to reach PENDING in child.

        For shapes with an upstream TASK, completes that TASK first.
        Returns (parent_run_id, child_run_id, child_ha_task_id).
        """
        parent_run_id = await _start_parent_run(deps)
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        child_run_id = parent_run.context.child_runs.get("wf-node")
        assert child_run_id, (
            f"No child run created for wf-node; child_runs={parent_run.context.child_runs}"
        )

        # Drain any upstream TASKs (e.g. child-pre-task) before HA can fire.
        # Loop because HA may only become PENDING after the upstream chain.
        for _ in range(5):
            child_tasks = deps["task_repo"].list_by_run(child_run_id)
            ha_pending = [
                t for t in child_tasks
                if t.node_id == "child-ha" and t.status == TaskStatus.PENDING
            ]
            if ha_pending:
                return parent_run_id, child_run_id, ha_pending[0].id

            running_non_ha = [
                t for t in child_tasks
                if t.status == TaskStatus.RUNNING and t.node_id != "child-ha"
            ]
            if not running_non_ha:
                break
            for t in running_non_ha:
                await _complete_task(deps, child_run_id, t.id)

        # Final check
        child_tasks = deps["task_repo"].list_by_run(child_run_id)
        ha_pending = [
            t for t in child_tasks
            if t.node_id == "child-ha" and t.status == TaskStatus.PENDING
        ]
        assert ha_pending, (
            f"child-ha did not reach PENDING after draining upstream; "
            f"tasks={[(t.node_id, t.status) for t in child_tasks]}"
        )
        return parent_run_id, child_run_id, ha_pending[0].id

    async def _resume_and_drain(
        self,
        deps,
        child_run_id: str,
        child_ha_task_id: str,
        action: str,
    ):
        """Resume the HA, then complete any downstream RUNNING tasks until
        the child quiesces.
        """
        resume_uc = _make_resume_use_case(deps)
        result = await resume_uc.execute(ResumePendingTaskCommand(
            run_id=child_run_id,
            task_id=child_ha_task_id,
            updated_input={"_action": action},
        ))
        assert result.resumed is True
        await _drain_queue(deps)

        # Drain RUNNING child tasks until none remain.
        for _ in range(5):
            child_run = deps["run_repo"].get_by_id(child_run_id)
            if child_run.status in (RunStatus.COMPLETED, RunStatus.FAILED):
                break
            running = [
                t for t in deps["task_repo"].list_by_run(child_run_id)
                if t.status == TaskStatus.RUNNING
            ]
            if not running:
                break
            for t in running:
                await _complete_task(deps, child_run_id, t.id)

    # ------------------------------------------------------------------
    # 1. TASK BEFORE HUMAN_ACTION
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_task_before_ha_confirm_completes_child_and_parent(self, deps):
        self._save_parent(deps)
        deps["workflow_repo"].save(_child_workflow_task_before_ha())

        parent_run_id, child_run_id, child_ha_task_id = (
            await self._drive_child_to_ha_pending(deps)
        )
        await self._resume_and_drain(deps, child_run_id, child_ha_task_id, "confirm")

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child should be COMPLETED on confirm; got {child_run.status}, "
            f"completed_nodes={child_run.context.completed_nodes}, "
            f"skipped_nodes={child_run.context.skipped_nodes}"
        )
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert parent_run.status == RunStatus.COMPLETED, (
            f"Parent should be COMPLETED; got {parent_run.status}, "
            f"completed_nodes={parent_run.context.completed_nodes}"
        )

    @pytest.mark.asyncio
    async def test_task_before_ha_reject_completes_child_and_parent(self, deps):
        self._save_parent(deps)
        deps["workflow_repo"].save(_child_workflow_task_before_ha())

        parent_run_id, child_run_id, child_ha_task_id = (
            await self._drive_child_to_ha_pending(deps)
        )
        await self._resume_and_drain(deps, child_run_id, child_ha_task_id, "reject")

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child should be COMPLETED on reject (HA is leaf); got {child_run.status}, "
            f"completed_nodes={child_run.context.completed_nodes}, "
            f"skipped_nodes={child_run.context.skipped_nodes}"
        )
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert parent_run.status == RunStatus.COMPLETED, (
            f"Parent should be COMPLETED; got {parent_run.status}"
        )

    # ------------------------------------------------------------------
    # 2. TASK AFTER HUMAN_ACTION (unconditional edge)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_task_after_ha_confirm_completes_child_and_parent(self, deps):
        self._save_parent(deps)
        deps["workflow_repo"].save(_child_workflow_task_after_ha_unconditional())

        parent_run_id, child_run_id, child_ha_task_id = (
            await self._drive_child_to_ha_pending(deps)
        )
        await self._resume_and_drain(deps, child_run_id, child_ha_task_id, "confirm")

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert "child-post-task" in child_run.context.completed_nodes, (
            f"child-post-task should be COMPLETED on confirm; "
            f"completed_nodes={child_run.context.completed_nodes}"
        )
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child should be COMPLETED; got {child_run.status}"
        )
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert parent_run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_task_after_ha_reject_completes_child_and_parent(self, deps):
        """Reject + unconditional edge from HA: a port-less outgoing edge
        is taken on BOTH actions — the resume use case classifies only
        explicit opposite-port edges as untaken ("a single-output HA
        always continues"). The downstream TASK therefore runs on reject
        too, and child + parent complete.
        """
        self._save_parent(deps)
        deps["workflow_repo"].save(_child_workflow_task_after_ha_unconditional())

        parent_run_id, child_run_id, child_ha_task_id = (
            await self._drive_child_to_ha_pending(deps)
        )
        await self._resume_and_drain(deps, child_run_id, child_ha_task_id, "reject")

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert "child-post-task" in child_run.context.completed_nodes, (
            f"port-less edge continues on reject — child-post-task should run; "
            f"completed_nodes={child_run.context.completed_nodes}"
        )
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child should be COMPLETED; got {child_run.status}, "
            f"completed_nodes={child_run.context.completed_nodes}, "
            f"skipped_nodes={child_run.context.skipped_nodes}"
        )
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert parent_run.status == RunStatus.COMPLETED

    # ------------------------------------------------------------------
    # 3. CONDITION BEFORE HUMAN_ACTION
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_condition_before_ha_confirm_completes_child_and_parent(self, deps):
        self._save_parent(deps)
        deps["workflow_repo"].save(_child_workflow_condition_before_ha())

        parent_run_id, child_run_id, child_ha_task_id = (
            await self._drive_child_to_ha_pending(deps)
        )
        # CONDITION should have evaluated to true and completed.
        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert "child-cond" in child_run.context.completed_nodes

        await self._resume_and_drain(deps, child_run_id, child_ha_task_id, "confirm")

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child should be COMPLETED on confirm; got {child_run.status}, "
            f"completed_nodes={child_run.context.completed_nodes}"
        )
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert parent_run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_condition_before_ha_reject_completes_child_and_parent(self, deps):
        self._save_parent(deps)
        deps["workflow_repo"].save(_child_workflow_condition_before_ha())

        parent_run_id, child_run_id, child_ha_task_id = (
            await self._drive_child_to_ha_pending(deps)
        )
        await self._resume_and_drain(deps, child_run_id, child_ha_task_id, "reject")

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child should be COMPLETED on reject (HA is leaf); got {child_run.status}, "
            f"completed_nodes={child_run.context.completed_nodes}"
        )
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert parent_run.status == RunStatus.COMPLETED

    # ------------------------------------------------------------------
    # 4. CONDITION AFTER HUMAN_ACTION (action-dependent branching)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_condition_after_ha_confirm_routes_to_then_branch(self, deps):
        """Confirm: CONDITION evaluates ``_action == 'confirm'`` → True →
        TASK_then is dispatched, TASK_else is skipped."""
        self._save_parent(deps)
        deps["workflow_repo"].save(_child_workflow_condition_after_ha())

        parent_run_id, child_run_id, child_ha_task_id = (
            await self._drive_child_to_ha_pending(deps)
        )
        await self._resume_and_drain(deps, child_run_id, child_ha_task_id, "confirm")

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert "child-then-task" in child_run.context.completed_nodes, (
            f"child-then-task should run on confirm; "
            f"completed={child_run.context.completed_nodes}, "
            f"skipped={child_run.context.skipped_nodes}"
        )
        assert "child-else-task" not in child_run.context.completed_nodes, (
            f"child-else-task should NOT run on confirm; "
            f"completed={child_run.context.completed_nodes}"
        )
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child should be COMPLETED; got {child_run.status}, "
            f"completed={child_run.context.completed_nodes}, "
            f"skipped={child_run.context.skipped_nodes}"
        )
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert parent_run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_condition_after_ha_reject_routes_to_else_branch(self, deps):
        """Reject: the port-less HA→CONDITION edge is taken on both
        actions ("single-output HA always continues"), so the CONDITION
        runs, evaluates ``_action == 'confirm'`` → False, and routes the
        else branch. then-task never runs (untaken branch, no skip
        bookkeeping under edge-based dispatch).
        """
        self._save_parent(deps)
        deps["workflow_repo"].save(_child_workflow_condition_after_ha())

        parent_run_id, child_run_id, child_ha_task_id = (
            await self._drive_child_to_ha_pending(deps)
        )
        await self._resume_and_drain(deps, child_run_id, child_ha_task_id, "reject")

        child_run = deps["run_repo"].get_by_id(child_run_id)
        assert "child-cond" in child_run.context.completed_nodes, (
            f"CONDITION should evaluate the action on reject; "
            f"completed={child_run.context.completed_nodes}"
        )
        assert "child-else-task" in child_run.context.completed_nodes, (
            f"else branch should run on reject; "
            f"completed={child_run.context.completed_nodes}"
        )
        assert "child-then-task" not in child_run.context.completed_nodes, (
            f"then branch must NOT run on reject; "
            f"completed={child_run.context.completed_nodes}"
        )
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child should be COMPLETED; got {child_run.status}, "
            f"completed={child_run.context.completed_nodes}, "
            f"skipped={child_run.context.skipped_nodes}"
        )
        parent_run = deps["run_repo"].get_by_id(parent_run_id)
        assert parent_run.status == RunStatus.COMPLETED


# ---------------------------------------------------------------------------
# Diagnostic test: confirm the file_ref shape that successor_processor sees
# ---------------------------------------------------------------------------

class TestHumanActionVariableShapeAfterResume:
    """After resume, ``run.context.variables['child-ha']`` should be a
    file_ref dict (production shape).  This test documents that shape so
    future regressions in DataStoreService wrapping are caught here.
    """

    @pytest.mark.asyncio
    async def test_variable_shape_is_file_ref_after_resume(self, deps):
        deps["workflow_repo"].save(_parent_workflow_calls_child())
        deps["workflow_repo"].save(_child_workflow_human_action_only())

        parent_run_id = await _start_parent_run(deps)
        child_run_id, child_ha_task_id = _get_child_state(deps, parent_run_id)

        resume_uc = _make_resume_use_case(deps)
        await resume_uc.execute(ResumePendingTaskCommand(
            run_id=child_run_id,
            task_id=child_ha_task_id,
            updated_input={"_action": "reject"},
        ))
        await _drain_queue(deps)

        # Phase 3b.1: node outputs live on the projector view, not the
        # run aggregate — read the production shape through the view store.
        view = deps["view_store"].get(child_run_id)
        ha_var = view.variables.get("child-ha")
        from app.layer1_domain.entities.data_reference import is_file_ref

        assert is_file_ref(ha_var), (
            f"child-ha variable should be a file_ref after data-store wrapping; "
            f"got: {type(ha_var).__name__} = {ha_var!r}"
        )
        # Critically: _action is NOT at the top level of the file_ref dict.
        # It lives inside extracted[]. Any code reading ha_var['_action']
        # directly (e.g. SuccessorProcessor._get_human_action_branch) would
        # see the absence of the key and default to "confirm".
        assert "_action" not in ha_var, (
            f"file_ref should not have _action at top level; got keys={list(ha_var.keys())}"
        )
        # The action should be retrievable via VariableResolver, which knows
        # how to read from file_ref.extracted.
        resolved = await deps["variable_resolver"].resolve(
            "{{$child-ha._action}}", dict(view.variables),
        )
        assert resolved == "reject", (
            f"VariableResolver should read _action from file_ref.extracted; got {resolved!r}"
        )
