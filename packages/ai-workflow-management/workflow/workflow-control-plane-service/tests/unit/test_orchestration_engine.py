"""Unit tests for orchestration_engine.py."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone

from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.events import RunStatusChanged, NodeFailed, EventTopic
from app.layer1_domain.exceptions import ExpressionError, InputRequirementError
from app.layer2_application.node_handlers.base import NodeHandlerResult, NodeHandlerRegistry
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor, ProcessingResult, PENDING_NODE_TYPES
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine


def _wire_async_repo_mocks(repo: MagicMock) -> MagicMock:
    """Make a MagicMock-based repo cooperate with the engine's new
    ``await repo.a<method>(...)`` calls (Task #19). The engine no
    longer calls sync methods on the repo; it awaits ``aget_by_id``,
    ``asave``, ``alist_by_run``. MagicMock auto-creates those
    attributes but as plain callables; ``await`` on a plain callable
    raises TypeError. Replace them with AsyncMocks that delegate to
    the existing sync return values so test setups like
    ``repo.list_by_run.return_value = [...]`` keep working unchanged.

    SA-2318 added ``latest_by_run_node`` — a point lookup that replaced a
    ``list_by_run`` + Python scan inside ``_active_task``. Its mock is DERIVED
    from ``list_by_run`` rather than auto-mocked, for two reasons: every
    existing setup here configures ``repo.list_by_run.return_value``, and a
    bare MagicMock would return a truthy non-Task that reads as "a task is in
    flight" and silently changes dispatch behaviour. Deriving it keeps those
    setups meaningful and keeps the double faithful to the real repositories.
    """
    if not isinstance(repo, MagicMock):
        return repo
    repo.aget_by_id = AsyncMock(side_effect=lambda *a, **kw: repo.get_by_id(*a, **kw))
    repo.asave = AsyncMock(side_effect=lambda *a, **kw: repo.save(*a, **kw))
    repo.alist_by_run = AsyncMock(
        side_effect=lambda *a, **kw: repo.list_by_run(*a, **kw),
    )

    def _latest_by_run_node(run_id, node_id):
        # Same selection the real repos make: newest by created_at, None
        # coalesced to "" so a timestamp-less task sorts oldest, strictly-
        # greater so the first of two equal timestamps wins.
        latest = None
        for t in repo.list_by_run(run_id) or []:
            if getattr(t, "node_id", None) != node_id:
                continue
            if latest is None or (t.created_at or "") > (latest.created_at or ""):
                latest = t
        return latest

    repo.latest_by_run_node = MagicMock(side_effect=_latest_by_run_node)
    repo.alatest_by_run_node = AsyncMock(side_effect=_latest_by_run_node)
    return repo


def _make_engine(
    handler_registry=None,
    task_factory=None,
    successor_processor=None,
    stage_recorder=None,
    task_repo=None,
    run_repo=None,
    event_publisher=None,
    callback_publisher=None,
    run_context_view_store=None,
):
    return OrchestrationEngine(
        handler_registry=handler_registry or MagicMock(),
        task_factory=task_factory or TaskFactory(),
        successor_processor=successor_processor or MagicMock(),
        stage_recorder=stage_recorder or MagicMock(),
        task_repo=_wire_async_repo_mocks(task_repo or MagicMock()),
        run_repo=_wire_async_repo_mocks(run_repo or MagicMock()),
        event_publisher=event_publisher,
        callback_publisher=callback_publisher,
        run_context_view_store=run_context_view_store,
    )


class _StaleViewStub:
    """Read-only view fixture: simulates a projector that hasn't yet
    applied the most recent ``NodeCompleted`` event. Only exposes the
    fields ``check_run_completion`` reads (``completed_nodes`` /
    ``skipped_nodes``); other ``IRunContextView`` properties are not
    consulted on this code path."""

    def __init__(
        self,
        completed_nodes: frozenset[str] = frozenset(),
        skipped_nodes: frozenset[str] = frozenset(),
    ) -> None:
        self.completed_nodes = completed_nodes
        self.skipped_nodes = skipped_nodes


class _StaleViewStoreStub:
    """View store that always returns the same stale view."""

    def __init__(self, view: _StaleViewStub) -> None:
        self._view = view

    def get(self, run_id: str) -> _StaleViewStub:
        return self._view


def _make_workflow_and_run():
    workflow = Workflow(
        id="wf-1",
        nodes=[
            WorkflowNode(id="trigger", name="Trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
        ],
        edges=[WorkflowEdge(source_node_id="trigger", target_node_id="task1")],
    )
    topology = GraphTopology(
        adjacency={"trigger": ["task1"], "task1": []},
        predecessors={"trigger": [], "task1": ["trigger"]},
        edge_map={"trigger": workflow.edges, "task1": []},
        entry_nodes=["trigger"],
        leaf_nodes=["task1"],
    )
    run = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(topology=topology),
    )
    return workflow, run, topology


class TestCheckRunCompletion:
    @pytest.mark.asyncio
    async def test_completed_run(self):
        workflow, run, topology = _make_workflow_and_run()
        run.context.completed_nodes = {"trigger", "task1"}

        task_repo = MagicMock()
        task_repo.list_by_run.return_value = [
            Task(id="t1", run_id="run-1", node_id="task1", status=TaskStatus.COMPLETED),
        ]
        run_repo = MagicMock()
        event_publisher = MagicMock()
        stage_recorder = MagicMock()

        engine = _make_engine(
            task_repo=task_repo,
            run_repo=run_repo,
            event_publisher=event_publisher,
            stage_recorder=stage_recorder,
        )

        await engine.check_run_completion(run, workflow)
        await engine.drain_events()  # publish_event buffers via EventCollector

        assert run.status == RunStatus.COMPLETED
        assert run.completed_at is not None
        run_repo.save.assert_called_once()
        stage_recorder.record.assert_called_once()
        event_publisher.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_completes_when_view_store_lags_in_memory_completion(self):
        """When a builtin handler (CONDITION/SWITCH/MERGE/etc.) is the
        run's last node, it completes synchronously inside ``_exec_uniform``
        and the outer caller immediately invokes ``check_run_completion``
        — before the projector has applied the ``NodeCompleted`` event.
        ``_view_completed`` must therefore union the in-memory aggregate
        (``run.context.completed_nodes``) with the view store's snapshot;
        otherwise the leaf check sees the just-completed leaf as missing
        and the run hangs in ``RUNNING``.
        """
        workflow, run, topology = _make_workflow_and_run()
        # Both leaf reachable nodes are done in the in-memory aggregate.
        run.context.completed_nodes = {"trigger", "task1"}
        # View store is stale: projector hasn't seen task1's completion yet.
        stale_view = _StaleViewStub(
            completed_nodes=frozenset({"trigger"}),
            skipped_nodes=frozenset(),
        )

        task_repo = MagicMock()
        task_repo.list_by_run.return_value = [
            Task(id="t1", run_id="run-1", node_id="task1", status=TaskStatus.COMPLETED),
        ]
        run_repo = MagicMock()
        event_publisher = MagicMock()

        engine = _make_engine(
            task_repo=task_repo,
            run_repo=run_repo,
            event_publisher=event_publisher,
            stage_recorder=MagicMock(),
            run_context_view_store=_StaleViewStoreStub(stale_view),
        )

        await engine.check_run_completion(run, workflow)

        assert run.status == RunStatus.COMPLETED
        assert run.completed_at is not None

    @pytest.mark.asyncio
    async def test_failed_run(self):
        workflow, run, topology = _make_workflow_and_run()
        run.context.completed_nodes = {"trigger"}

        task_repo = MagicMock()
        task_repo.list_by_run.return_value = [
            Task(id="t1", run_id="run-1", node_id="task1", status=TaskStatus.FAILED),
        ]
        run_repo = MagicMock()
        stage_recorder = MagicMock()

        engine = _make_engine(
            task_repo=task_repo,
            run_repo=run_repo,
            stage_recorder=stage_recorder,
        )

        await engine.check_run_completion(run, workflow)

        assert run.status == RunStatus.FAILED
        assert run.completed_at is not None

    @pytest.mark.asyncio
    async def test_still_active(self):
        workflow, run, topology = _make_workflow_and_run()
        run.context.completed_nodes = {"trigger"}

        task_repo = MagicMock()
        task_repo.list_by_run.return_value = [
            Task(id="t1", run_id="run-1", node_id="task1", status=TaskStatus.RUNNING),
        ]

        engine = _make_engine(task_repo=task_repo)

        await engine.check_run_completion(run, workflow)

        assert run.status == RunStatus.RUNNING

    @pytest.mark.asyncio
    async def test_already_non_running(self):
        workflow, run, topology = _make_workflow_and_run()
        run.status = RunStatus.COMPLETED

        engine = _make_engine()

        await engine.check_run_completion(run, workflow)

        # Should return early, status unchanged
        assert run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_failed_task_with_skipped_marker_does_not_fail_run(self):
        """A failed task with output_metadata.skipped should not cause run failure."""
        workflow, run, topology = _make_workflow_and_run()
        run.context.completed_nodes = {"trigger", "task1"}

        task_repo = MagicMock()
        task_repo.list_by_run.return_value = [
            Task(
                id="t1", run_id="run-1", node_id="task1",
                status=TaskStatus.FAILED,
                output_metadata={"skipped": True},
            ),
        ]
        run_repo = MagicMock()
        event_publisher = MagicMock()
        stage_recorder = MagicMock()

        engine = _make_engine(
            task_repo=task_repo,
            run_repo=run_repo,
            event_publisher=event_publisher,
            stage_recorder=stage_recorder,
        )

        await engine.check_run_completion(run, workflow)

        assert run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_deleted_node_failed_task_does_not_block_completion(self):
        """A failed task for a node deleted from the workflow should not cause run failure."""
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="trigger", name="Trigger", node_type=NodeType.TRIGGER),
                WorkflowNode(id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
            ],
            edges=[WorkflowEdge(source_node_id="trigger", target_node_id="task1")],
        )
        # Stale topology still references deleted "task2"
        topology = GraphTopology(
            adjacency={"trigger": ["task1"], "task1": ["task2"], "task2": []},
            predecessors={"trigger": [], "task1": ["trigger"], "task2": ["task1"]},
            edge_map={"trigger": [], "task1": [], "task2": []},
            entry_nodes=["trigger"],
            leaf_nodes=["task2"],
        )
        run = WorkflowRun(
            id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING,
            context=RunContext(
                topology=topology,
                completed_nodes={"trigger", "task1"},
            ),
        )

        task_repo = MagicMock()
        task_repo.list_by_run.return_value = [
            Task(id="t1", run_id="run-1", node_id="task1", status=TaskStatus.COMPLETED),
            Task(id="t2", run_id="run-1", node_id="task2", status=TaskStatus.FAILED),
        ]
        run_repo = MagicMock()
        event_publisher = MagicMock()
        stage_recorder = MagicMock()

        engine = _make_engine(
            task_repo=task_repo, run_repo=run_repo,
            event_publisher=event_publisher, stage_recorder=stage_recorder,
        )

        await engine.check_run_completion(run, workflow)

        assert run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_deleted_leaf_node_allows_completion(self):
        """When a leaf node is deleted, the run should complete if the new leaves are covered."""
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="trigger", name="Trigger", node_type=NodeType.TRIGGER),
                WorkflowNode(id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
            ],
            edges=[WorkflowEdge(source_node_id="trigger", target_node_id="task1")],
        )
        topology = GraphTopology(
            adjacency={"trigger": ["task1"], "task1": ["task2"], "task2": []},
            predecessors={"trigger": [], "task1": ["trigger"], "task2": ["task1"]},
            edge_map={"trigger": [], "task1": [], "task2": []},
            entry_nodes=["trigger"],
            leaf_nodes=["task2"],
        )
        run = WorkflowRun(
            id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING,
            context=RunContext(
                topology=topology,
                completed_nodes={"trigger", "task1"},
            ),
        )

        task_repo = MagicMock()
        task_repo.list_by_run.return_value = [
            Task(id="t1", run_id="run-1", node_id="task1", status=TaskStatus.COMPLETED),
        ]
        run_repo = MagicMock()
        event_publisher = MagicMock()
        stage_recorder = MagicMock()

        engine = _make_engine(
            task_repo=task_repo, run_repo=run_repo,
            event_publisher=event_publisher, stage_recorder=stage_recorder,
        )

        await engine.check_run_completion(run, workflow)

        assert run.status == RunStatus.COMPLETED
        assert "task2" not in run.context.topology.leaf_nodes
        assert "task1" in run.context.topology.leaf_nodes

    @pytest.mark.asyncio
    async def test_deleted_node_active_task_does_not_block_completion(self):
        """An active (RUNNING) task for a deleted node should not prevent completion."""
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="trigger", name="Trigger", node_type=NodeType.TRIGGER),
                WorkflowNode(id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
            ],
            edges=[WorkflowEdge(source_node_id="trigger", target_node_id="task1")],
        )
        topology = GraphTopology(
            adjacency={"trigger": ["task1"], "task1": ["task2"], "task2": []},
            predecessors={"trigger": [], "task1": ["trigger"], "task2": ["task1"]},
            edge_map={"trigger": [], "task1": [], "task2": []},
            entry_nodes=["trigger"],
            leaf_nodes=["task2"],
        )
        run = WorkflowRun(
            id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING,
            context=RunContext(
                topology=topology,
                completed_nodes={"trigger", "task1"},
            ),
        )

        task_repo = MagicMock()
        task_repo.list_by_run.return_value = [
            Task(id="t1", run_id="run-1", node_id="task1", status=TaskStatus.COMPLETED),
            Task(id="t2", run_id="run-1", node_id="task2", status=TaskStatus.RUNNING),
        ]
        run_repo = MagicMock()
        event_publisher = MagicMock()
        stage_recorder = MagicMock()

        engine = _make_engine(
            task_repo=task_repo, run_repo=run_repo,
            event_publisher=event_publisher, stage_recorder=stage_recorder,
        )

        await engine.check_run_completion(run, workflow)

        assert run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_skipped_leaf_active_task_does_not_block_completion(self):
        """An active task on a skipped branch leaf should not keep the run open."""
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="cond", name="Condition", node_type=NodeType.CONDITION),
                WorkflowNode(id="true-task", name="TrueTask", node_type=NodeType.TASK, worker_type="w1"),
                WorkflowNode(id="false-task", name="FalseTask", node_type=NodeType.TASK, worker_type="w2"),
            ],
            edges=[
                WorkflowEdge(source_node_id="cond", target_node_id="true-task", source_port_id="true_branch"),
                WorkflowEdge(source_node_id="cond", target_node_id="false-task", source_port_id="false_branch"),
            ],
        )
        topology = GraphTopology(
            adjacency={"cond": ["true-task", "false-task"], "true-task": [], "false-task": []},
            predecessors={"cond": [], "true-task": ["cond"], "false-task": ["cond"]},
            edge_map={"cond": workflow.edges, "true-task": [], "false-task": []},
            entry_nodes=["cond"],
            leaf_nodes=["true-task", "false-task"],
        )
        run = WorkflowRun(
            id="run-1",
            workflow_id="wf-1",
            status=RunStatus.RUNNING,
            context=RunContext(
                topology=topology,
                completed_nodes={"cond", "false-task"},
                skipped_nodes={"true-task"},
            ),
        )

        task_repo = MagicMock()
        task_repo.list_by_run.return_value = [
            Task(id="t-false", run_id="run-1", node_id="false-task", status=TaskStatus.COMPLETED),
            Task(id="t-true", run_id="run-1", node_id="true-task", status=TaskStatus.READY),
        ]
        run_repo = MagicMock()
        event_publisher = MagicMock()
        stage_recorder = MagicMock()

        engine = _make_engine(
            task_repo=task_repo,
            run_repo=run_repo,
            event_publisher=event_publisher,
            stage_recorder=stage_recorder,
        )

        await engine.check_run_completion(run, workflow)

        assert run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_skipped_leaf_failed_task_does_not_fail_run(self):
        """A failed task on a skipped branch leaf should not fail the run."""
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="cond", name="Condition", node_type=NodeType.CONDITION),
                WorkflowNode(id="true-task", name="TrueTask", node_type=NodeType.TASK, worker_type="w1"),
                WorkflowNode(id="false-task", name="FalseTask", node_type=NodeType.TASK, worker_type="w2"),
            ],
            edges=[
                WorkflowEdge(source_node_id="cond", target_node_id="true-task", source_port_id="true_branch"),
                WorkflowEdge(source_node_id="cond", target_node_id="false-task", source_port_id="false_branch"),
            ],
        )
        topology = GraphTopology(
            adjacency={"cond": ["true-task", "false-task"], "true-task": [], "false-task": []},
            predecessors={"cond": [], "true-task": ["cond"], "false-task": ["cond"]},
            edge_map={"cond": workflow.edges, "true-task": [], "false-task": []},
            entry_nodes=["cond"],
            leaf_nodes=["true-task", "false-task"],
        )
        run = WorkflowRun(
            id="run-1",
            workflow_id="wf-1",
            status=RunStatus.RUNNING,
            context=RunContext(
                topology=topology,
                completed_nodes={"cond", "false-task"},
                skipped_nodes={"true-task"},
            ),
        )

        task_repo = MagicMock()
        task_repo.list_by_run.return_value = [
            Task(id="t-false", run_id="run-1", node_id="false-task", status=TaskStatus.COMPLETED),
            Task(id="t-true", run_id="run-1", node_id="true-task", status=TaskStatus.FAILED, error="stale branch failure"),
        ]
        run_repo = MagicMock()
        event_publisher = MagicMock()
        stage_recorder = MagicMock()

        engine = _make_engine(
            task_repo=task_repo,
            run_repo=run_repo,
            event_publisher=event_publisher,
            stage_recorder=stage_recorder,
        )

        await engine.check_run_completion(run, workflow)

        assert run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_backstop_sweep_skips_stranded_cycle_with_unreachable_leaves(self):
        """Validation-loop bug: a back-edge cycle in the un-taken branch leaves
        nodes stranded — neither completed nor skipped. Under the old
        "all leaves covered" gate this prevented run completion and needed a
        backstop sweep. Edge-based dispatch retired both: completion is now
        decided by QUIESCENCE, so stranded nodes (which never received a task)
        cannot block it — the run completes without any sweep.

        Reproduces the user's "Data Validation and Transformation" workflow:
        4 leaves (F, P, L, Q); cycle H↔J in the E.false subtree.
        Path taken: N=true → B → E=true → M=false → P. P completed.
        The legacy cascade only managed to skip {C, D, F} — H, I, J, O, K, L, Q
        are stranded. The sweep should fix this.
        """
        node_ids = ["N", "B", "E", "M", "D", "F", "P",
                    "C", "H", "I", "J", "O", "K", "L", "Q"]
        condition_ids = {"N", "E", "M", "I", "O"}
        nodes = [
            WorkflowNode(
                id=nid, name=nid,
                node_type=(NodeType.CONDITION if nid in condition_ids else NodeType.TASK),
                worker_type=("" if nid in condition_ids else "w"),
            )
            for nid in node_ids
        ]
        edges = [
            WorkflowEdge(source_node_id="N", target_node_id="B", source_port_id="true_branch"),
            WorkflowEdge(source_node_id="N", target_node_id="M", source_port_id="false_branch"),
            WorkflowEdge(source_node_id="B", target_node_id="E"),
            WorkflowEdge(source_node_id="E", target_node_id="M", source_port_id="true_branch"),
            WorkflowEdge(source_node_id="E", target_node_id="C", source_port_id="false_branch"),
            WorkflowEdge(source_node_id="M", target_node_id="D", source_port_id="true_branch"),
            WorkflowEdge(source_node_id="M", target_node_id="P", source_port_id="false_branch"),
            WorkflowEdge(source_node_id="D", target_node_id="F"),
            WorkflowEdge(source_node_id="C", target_node_id="H"),
            WorkflowEdge(source_node_id="H", target_node_id="I"),
            WorkflowEdge(source_node_id="I", target_node_id="O", source_port_id="true_branch"),
            WorkflowEdge(source_node_id="I", target_node_id="J", source_port_id="false_branch"),
            WorkflowEdge(source_node_id="J", target_node_id="H"),
            WorkflowEdge(source_node_id="O", target_node_id="K", source_port_id="true_branch"),
            WorkflowEdge(source_node_id="O", target_node_id="Q", source_port_id="false_branch"),
            WorkflowEdge(source_node_id="K", target_node_id="L"),
        ]
        workflow = Workflow(id="wf-1", nodes=nodes, edges=edges)
        run = WorkflowRun(
            id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING,
            context=RunContext(
                # Path actually run: N → B → E → M → P. All in completed.
                completed_nodes={"N", "B", "E", "M", "P"},
                triggered_nodes={"N", "B", "E", "M", "P"},
                # The buggy legacy cascade only managed to skip these.
                skipped_nodes={"C", "D", "F"},
            ),
        )

        task_repo = MagicMock()
        task_repo.list_by_run.return_value = [
            Task(id=f"t-{nid}", run_id="run-1", node_id=nid, status=TaskStatus.COMPLETED)
            for nid in ("N", "B", "E", "M", "P")
        ]
        run_repo = MagicMock()
        event_publisher = MagicMock()
        stage_recorder = MagicMock()

        engine = _make_engine(
            task_repo=task_repo, run_repo=run_repo,
            event_publisher=event_publisher, stage_recorder=stage_recorder,
        )

        await engine.check_run_completion(run, workflow)

        # Edge-based dispatch: completion is decided by QUIESCENCE, not by an
        # "all leaves covered" gate + backstop sweep (both retired). The
        # stranded cycle nodes (H, I, J, O, K, L, Q) never received a task —
        # no incoming edge fired — so they hold no active task and cannot keep
        # the run from reaching quiescence. With only COMPLETED tasks and no
        # failures, the run completes despite the stranded cycle.
        assert run.status == RunStatus.COMPLETED
        assert run.completed_at is not None
        # The sweep no longer manufactures skipped entries for the stranded
        # nodes; the legacy cascade's {C, D, F} are all that's recorded.
        assert not ({"H", "I", "J", "O", "K", "L", "Q"} & run.context.completed_nodes)


class TestPublishCallback:
    def test_with_callback_config(self):
        callback_publisher = MagicMock()
        engine = _make_engine(callback_publisher=callback_publisher)

        run = WorkflowRun(
            id="run-1",
            workflow_id="wf-1",
            correlation_id="corr-1",
            callback_topic="agent.callback",
        )

        engine.publish_callback(run, "COMPLETED")

        callback_publisher.publish.assert_called_once()
        call_args = callback_publisher.publish.call_args
        assert call_args[0][0] == "agent.callback"
        assert call_args[1]["key"] == "corr-1"

    def test_without_callback_config(self):
        callback_publisher = MagicMock()
        engine = _make_engine(callback_publisher=callback_publisher)

        run = WorkflowRun(id="run-1", workflow_id="wf-1")

        engine.publish_callback(run, "COMPLETED")

        callback_publisher.publish.assert_not_called()

    def test_without_callback_publisher(self):
        engine = _make_engine(callback_publisher=None)

        run = WorkflowRun(
            id="run-1",
            workflow_id="wf-1",
            correlation_id="corr-1",
            callback_topic="agent.callback",
        )

        engine.publish_callback(run, "COMPLETED")
        # No exception


class TestPublishEvents:
    """publish_event/publish_events are the IMMEDIATE (non-buffering) aliases —
    they reach the publisher right away via _publish_now, so an event can never
    strand at a call site lacking a drain boundary. The buffering path is
    emit (singular) / emit + drain_events. See
    test_orchestration_engine_event_buffering.py for the full contract.
    """

    @pytest.mark.asyncio
    async def test_publish_event(self):
        event_publisher = MagicMock()
        engine = _make_engine(event_publisher=event_publisher)

        engine.publish_event("test.topic", {"data": 1})
        # Immediate: fired without waiting for a drain boundary.
        event_publisher.publish.assert_called_once_with("test.topic", {"data": 1})

    @pytest.mark.asyncio
    async def test_publish_event_no_publisher(self):
        engine = _make_engine(event_publisher=None)
        engine.publish_event("test.topic", {"data": 1})
        await engine.drain_events()
        # No exception

    @pytest.mark.asyncio
    async def test_publish_events_list(self):
        event_publisher = MagicMock()
        engine = _make_engine(event_publisher=event_publisher)

        events = [
            RunStatusChanged(run_id="r1", workflow_id="wf-1", status="completed"),
            RunStatusChanged(run_id="r2", workflow_id="wf-2", status="failed"),
        ]
        engine.publish_events(events)
        # Immediate: both fire right away (topic derived from each event).
        assert event_publisher.publish.call_count == 2

    @pytest.mark.asyncio
    async def test_publish_events_no_publisher(self):
        engine = _make_engine(event_publisher=None)
        engine.publish_events([RunStatusChanged(run_id="r1", workflow_id="wf-1", status="completed")])
        await engine.drain_events()
        # No exception

    @pytest.mark.asyncio
    async def test_publish_events_empty_list(self):
        event_publisher = MagicMock()
        engine = _make_engine(event_publisher=event_publisher)
        engine.publish_events([])
        await engine.drain_events()
        event_publisher.publish.assert_not_called()


class TestIsBuiltinNode:
    def test_builtin_trigger(self):
        workflow = Workflow(
            id="wf",
            nodes=[WorkflowNode(id="t", node_type=NodeType.TRIGGER)],
        )
        assert OrchestrationEngine.is_builtin_node("t", workflow) is True

    def test_task_node_not_builtin(self):
        workflow = Workflow(
            id="wf",
            nodes=[WorkflowNode(id="t", node_type=NodeType.TASK, worker_type="w")],
        )
        assert OrchestrationEngine.is_builtin_node("t", workflow) is False

    def test_unknown_node_id(self):
        workflow = Workflow(id="wf", nodes=[])
        assert OrchestrationEngine.is_builtin_node("missing", workflow) is False

    def test_all_builtin_types(self):
        for nt in [NodeType.CONDITION, NodeType.SWITCH, NodeType.LOOP,
                    NodeType.PARALLEL, NodeType.MERGE, NodeType.TRIGGER,
                    NodeType.WORKFLOW, NodeType.COMPUTE]:
            workflow = Workflow(
                id="wf",
                nodes=[WorkflowNode(id="n", node_type=nt)],
            )
            assert OrchestrationEngine.is_builtin_node("n", workflow) is True




class TestProcessAndDispatchSuccessors:
    """Tests for process_and_dispatch_successors handling of PENDING nodes."""

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_direct_pending_successor_not_double_dispatched(self):
        """When successor_processor already ran the PENDING handler (task has
        PENDING status), process_and_dispatch_successors should NOT call
        dispatch_or_handle_task again."""
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
                WorkflowNode(id="ha1", name="HumanAction1", node_type=NodeType.HUMAN_ACTION),
            ],
            edges=[
                WorkflowEdge(source_node_id="task1", target_node_id="ha1"),
            ],
        )
        topology = GraphTopology(
            adjacency={"task1": ["ha1"], "ha1": []},
            predecessors={"task1": [], "ha1": ["task1"]},
            edge_map={
                "task1": [workflow.edges[0]],
                "ha1": [],
            },
            entry_nodes=["task1"],
            leaf_nodes=["ha1"],
        )
        run = WorkflowRun(
            id="run-1",
            workflow_id="wf-1",
            status=RunStatus.RUNNING,
            context=RunContext(topology=topology),
        )
        run.context.completed_nodes.add("task1")

        # Successor processor already ran the handler — task has PENDING status
        ha_task = Task(id="t-ha1", run_id="run-1", node_id="ha1", status=TaskStatus.PENDING)

        successor_processor = AsyncMock()
        successor_processor.process.return_value = ProcessingResult(
            tasks_to_dispatch=[ha_task],
            events=[],
        )

        registry = MagicMock()
        task_repo = MagicMock()
        # Start empty so the edge-based dispatch dedup check sees no
        # existing task; once ``save`` is called, ``list_by_run`` flips
        # to return the saved task so the quiescence completion check
        # sees the PENDING task as active and the run stays running.
        _saved: list = []
        task_repo.list_by_run.side_effect = lambda *_a, **_kw: list(_saved)
        task_repo.save.side_effect = lambda t: _saved.append(t)
        run_repo = MagicMock()
        stage_recorder = MagicMock()

        engine = _make_engine(
            handler_registry=registry,
            successor_processor=successor_processor,
            task_repo=task_repo,
            run_repo=run_repo,
            stage_recorder=stage_recorder,
        )

        completed_task = Task(id="t-task1", run_id="run-1", node_id="task1", status=TaskStatus.COMPLETED)

        await engine.process_and_dispatch_successors(
            completed_task, run, workflow, topology
        )

        # Handler should NOT be called again (no dispatch_or_handle_task)
        registry.get.assert_not_called()
        # Stage records for the PENDING task — exactly one stage per
        # task.pending_input transition; quiescence does NOT fire because
        # the PENDING task counts as active.
        pending_calls = [
            c for c in stage_recorder.record.call_args_list
            if c.kwargs.get("event_type") == "task.pending_input"
        ]
        assert len(pending_calls) == 1


class TestExecuteNodeInnerIdempotency:
    """``_execute_node_inner`` drops a redelivered ExecuteNodeAction.

    The orchestration queue is at-least-once with no dedup key, so the same
    ExecuteNodeAction can be re-delivered (worker retry / broker redrive). A task
    that has already paused (PENDING) or reached a terminal status is never a
    legitimate fresh dispatch target, so re-delivery there is a duplicate and must
    NOT re-run its (possibly non-idempotent) handler. RUNNING is excluded because
    ``RerunTaskUseCase`` deliberately re-executes the same task id with RUNNING
    status; READY/WAITING are the pre-execution statuses a first dispatch uses.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        [
            TaskStatus.PENDING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ],
    )
    async def test_redelivered_execute_skips_already_executed_task(self, status):
        _workflow, run, _topology = _make_workflow_and_run()
        task = Task(id="run-1-task1", run_id="run-1", node_id="task1", status=status)
        run_repo = MagicMock()
        run_repo.get_by_id.return_value = run
        task_repo = MagicMock()
        task_repo.get_by_id.return_value = task

        engine = _make_engine(run_repo=run_repo, task_repo=task_repo)
        engine._exec_uniform = AsyncMock()
        engine.load_workflow_for_run = MagicMock()

        await engine._execute_node_inner("run-1", "run-1-task1")

        # Guard returns before any execution work — the handler never re-fires.
        engine._exec_uniform.assert_not_awaited()
        engine.load_workflow_for_run.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [TaskStatus.READY, TaskStatus.RUNNING])
    async def test_executes_ready_and_running_tasks(self, status):
        # READY = first dispatch; RUNNING = RerunTaskUseCase re-executing the same
        # task id (rerun_task_use_case.py sets RUNNING then calls
        # orchestrate_execute_node). Both must run, not be dropped as duplicates.
        _workflow, run, _topology = _make_workflow_and_run()
        task = Task(id="run-1-task1", run_id="run-1", node_id="task1", status=status)
        run_repo = MagicMock()
        run_repo.get_by_id.return_value = run
        task_repo = MagicMock()
        task_repo.get_by_id.return_value = task

        engine = _make_engine(run_repo=run_repo, task_repo=task_repo)
        wf = MagicMock()
        wf.get_node.return_value = WorkflowNode(
            id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1",
        )
        engine.load_workflow_for_run = MagicMock(return_value=wf)
        engine._exec_uniform = AsyncMock()
        engine.drain_events = AsyncMock()
        engine.check_run_completion = AsyncMock()

        await engine._execute_node_inner("run-1", "run-1-task1")

        engine._exec_uniform.assert_awaited_once()


# ────────────────────────── locked_run reentrancy ──────────────────────────


class TestLockedRunReentrancy:
    """``locked_run`` — reentrant per-run critical section (SA-846).

    ``ChildWorkflowCoordinator.complete_parent``/``fail_parent`` mutate
    parent state (node_visits bumps, successor task minting) from the
    child's orchestration context, so they acquire the PARENT run's
    lock. A trivially-empty child can complete synchronously inside
    ``coordinator.start`` — i.e. under the parent's already-held lock —
    which a plain ``asyncio.Lock`` would deadlock on.
    """

    @pytest.mark.asyncio
    async def test_nested_same_run_id_reenters(self):
        engine = _make_engine()
        async with engine.locked_run("run-1"):
            assert engine.run_lock("run-1").locked()
            # Re-entering the same run id must not deadlock.
            async with engine.locked_run("run-1"):
                assert engine.run_lock("run-1").locked()
            # Inner exit must not release the outer hold.
            assert engine.run_lock("run-1").locked()
        assert not engine.run_lock("run-1").locked()

    @pytest.mark.asyncio
    async def test_cross_task_acquisition_serializes(self):
        import asyncio

        engine = _make_engine()
        order: list[str] = []
        entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with engine.locked_run("run-1"):
                order.append("holder-in")
                entered.set()
                await release.wait()
                order.append("holder-out")

        async def contender():
            await entered.wait()
            async with engine.locked_run("run-1"):
                order.append("contender-in")

        h = asyncio.create_task(holder())
        c = asyncio.create_task(contender())
        await entered.wait()
        await asyncio.sleep(0)  # let the contender block on the lock
        release.set()
        await asyncio.gather(h, c)
        assert order == ["holder-in", "holder-out", "contender-in"]

    @pytest.mark.asyncio
    async def test_complete_parent_acquires_parent_lock(self):
        """The coordinator's parent-side section runs while holding the
        PARENT run's lock — the duplicate ``-v{n}`` window between a
        child completion and concurrent parent orchestration is closed."""
        from app.layer2_application.orchestration.orchestration_engine import (
            _held_run_locks,
        )

        engine = _make_engine()
        coord = engine._child_coord
        captured: list[frozenset] = []

        async def fake_resolve(child_run, **kwargs):
            captured.append(_held_run_locks.get())
            return None

        coord._resolve_parent_context = fake_resolve

        child_run = WorkflowRun(
            id="child-1", workflow_id="wf-c", status=RunStatus.COMPLETED,
            parent_run_id="parent-1", parent_node_id="wf-node",
            context=RunContext(variables={}),
        )
        await coord.complete_parent(child_run)
        await coord.fail_parent(child_run)
        assert len(captured) == 2
        assert all("parent-1" in held for held in captured)
        # Released after the call.
        assert not engine.run_lock("parent-1").locked()

    @pytest.mark.asyncio
    async def test_complete_parent_reenters_held_parent_lock(self):
        """Synchronous child completion inside ``coordinator.start``
        (parent lock already held by this task) must re-enter, not
        deadlock."""
        import asyncio

        engine = _make_engine()
        coord = engine._child_coord

        async def fake_resolve(child_run, **kwargs):
            return None

        coord._resolve_parent_context = fake_resolve
        child_run = WorkflowRun(
            id="child-1", workflow_id="wf-c", status=RunStatus.COMPLETED,
            parent_run_id="parent-1", parent_node_id="wf-node",
            context=RunContext(variables={}),
        )
        async with engine.locked_run("parent-1"):
            await asyncio.wait_for(coord.complete_parent(child_run), timeout=2)
            assert engine.run_lock("parent-1").locked()

    @pytest.mark.asyncio
    async def test_no_parent_run_id_is_noop(self):
        engine = _make_engine()
        coord = engine._child_coord
        root_run = WorkflowRun(
            id="root-1", workflow_id="wf-r", status=RunStatus.COMPLETED,
            context=RunContext(variables={}),
        )
        # Must not raise nor touch any lock.
        await coord.complete_parent(root_run)
        await coord.fail_parent(root_run)
