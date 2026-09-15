"""Tests for rerun_task feature."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.rerun_task.use_cases.rerun_task_use_case import (
    RerunTaskUseCase,
    RerunTaskCommand,
)

from tests.conftest import DummyLogger, DummyEventPublisher, DummyStageRecorder
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine


# ── Helpers ──────────────────────────────────────────────────────


class MemRepo:
    def __init__(self):
        self._store = {}

    def save(self, entity):
        self._store[entity.id] = entity

    def get_by_id(self, id_):
        return self._store.get(id_)

    def list_by_run(self, run_id):
        return [e for e in self._store.values() if getattr(e, "run_id", None) == run_id]

    # Async wrappers — async-IO conversion in Task #20 routes
    # use-case calls through ``aget_by_id`` / ``asave`` /
    # ``alist_by_run``. In-memory ops don't benefit from executor
    # offload; just delegate.
    async def aget_by_id(self, id_):
        return self.get_by_id(id_)

    async def asave(self, entity):
        self.save(entity)

    async def alist_by_run(self, run_id):
        return self.list_by_run(run_id)


class _SpyEngine(IOrchestrationEngine):
    def __init__(self, workflow_repo=None):
        self.rerun_calls: list = []
        self.dispatched_calls: list = []
        # Re-resolve path: rerun routes through orchestrate_execute_node so
        # the handler recomputes input from the (possibly edited) node.
        self.reexecute_calls: list = []
        self._workflow_repo = workflow_repo
        # No orchestration queue wired on this stub → rerun falls back to
        # the inline ``orchestrate_execute_node`` branch.
        self._queue = None

    def load_workflow_for_run(self, run):
        # Test stub: read off the live workflow_repo (snapshot capture
        # is irrelevant here — tests construct ``WorkflowRun`` directly).
        if self._workflow_repo is None:
            return None
        return self._workflow_repo.get_by_id(run.workflow_id)

    def emit_node_dispatched(self, task, run, node=None, *, attempt=1, dispatch_reason=""):
        self.dispatched_calls.append((task, run, node))
        if dispatch_reason == "rerun":
            self.rerun_calls.append((task, run, dispatch_reason))

    async def orchestrate_execute_node(self, run_id, task_id):
        self.reexecute_calls.append((run_id, task_id))


def _make_engine(workflow_repo=None):
    return _SpyEngine(workflow_repo=workflow_repo)


def _make_workflow():
    return Workflow(
        id="wf-1",
        name="Test WF",
        nodes=[
            WorkflowNode(id="n1", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
            WorkflowNode(id="n2", name="Task2", node_type=NodeType.TASK, worker_type="w2"),
        ],
        edges=[
            WorkflowEdge(source_node_id="n1", target_node_id="n2"),
        ],
    )


def _make_run(status=RunStatus.RUNNING, completed_nodes=None):
    topology = GraphTopology(
        adjacency={"n1": ["n2"], "n2": []},
        predecessors={"n1": [], "n2": ["n1"]},
        entry_nodes=["n1"],
        leaf_nodes=["n2"],
    )
    return WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        status=status,
        context=RunContext(
            variables={"input": {"x": 1}},
            topology=topology,
            completed_nodes=set(completed_nodes or []),
        ),
    )


def _make_task(status=TaskStatus.FAILED, node_id="n1"):
    return Task(
        id="task-1",
        run_id="run-1",
        node_id=node_id,
        status=status,
        worker_type="w1",
        resolved_input={"x": 1},
        idempotency_key="task-1-attempt-1",
    )


def _build_deps():
    workflow_repo = MemRepo()
    return {
        "logger": DummyLogger(),
        "workflow_repo": workflow_repo,
        "run_repo": MemRepo(),
        "task_repo": MemRepo(),
        "stage_recorder": DummyStageRecorder(),
        "orchestration_engine": _make_engine(workflow_repo=workflow_repo),
    }


# ── Tests ────────────────────────────────────────────────────────


class TestRerunTask:

    @pytest.mark.asyncio
    async def test_run_not_found(self):
        deps = _build_deps()
        uc = RerunTaskUseCase(**deps)
        result = await uc.execute(RerunTaskCommand(run_id="missing", task_id="t1"))
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_task_not_found(self):
        deps = _build_deps()
        run = _make_run()
        deps["run_repo"].save(run)

        uc = RerunTaskUseCase(**deps)
        result = await uc.execute(RerunTaskCommand(run_id="run-1", task_id="missing"))
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_task_not_in_run(self):
        deps = _build_deps()
        run = _make_run()
        deps["run_repo"].save(run)

        task = Task(id="task-1", run_id="other-run", node_id="n1", status=TaskStatus.FAILED)
        deps["task_repo"].save(task)

        uc = RerunTaskUseCase(**deps)
        result = await uc.execute(RerunTaskCommand(run_id="run-1", task_id="task-1"))
        assert "does not belong" in result.error

    @pytest.mark.asyncio
    async def test_cannot_rerun_running_task(self):
        deps = _build_deps()
        run = _make_run()
        deps["run_repo"].save(run)
        task = _make_task(status=TaskStatus.RUNNING)
        deps["task_repo"].save(task)
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)

        uc = RerunTaskUseCase(**deps)
        result = await uc.execute(RerunTaskCommand(run_id="run-1", task_id="task-1"))
        assert result.error_code == "INVALID_TASK_STATUS"

    @pytest.mark.asyncio
    async def test_cannot_rerun_on_cancelled_run(self):
        deps = _build_deps()
        run = _make_run(status=RunStatus.CANCELLED)
        deps["run_repo"].save(run)
        task = _make_task(status=TaskStatus.FAILED)
        deps["task_repo"].save(task)
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)

        uc = RerunTaskUseCase(**deps)
        result = await uc.execute(RerunTaskCommand(run_id="run-1", task_id="task-1"))
        assert result.error_code == "INVALID_RUN_STATUS"

    @pytest.mark.asyncio
    async def test_cannot_rerun_builtin_node(self):
        deps = _build_deps()
        wf = Workflow(
            id="wf-1", name="Test",
            nodes=[WorkflowNode(id="c1", name="Cond", node_type=NodeType.CONDITION)],
            edges=[],
        )
        deps["workflow_repo"].save(wf)
        run = _make_run()
        deps["run_repo"].save(run)
        task = Task(id="task-1", run_id="run-1", node_id="c1", status=TaskStatus.FAILED)
        deps["task_repo"].save(task)

        uc = RerunTaskUseCase(**deps)
        result = await uc.execute(RerunTaskCommand(run_id="run-1", task_id="task-1"))
        assert result.error_code == "INVALID_TASK_STATUS"
        assert result.error_details.get("reason") == "builtin_node"

    @pytest.mark.asyncio
    async def test_successful_rerun_failed_task(self):
        deps = _build_deps()
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)
        run = _make_run(status=RunStatus.FAILED, completed_nodes=["n1"])
        run.context.variables["n1"] = {"output": {"uri": "old-ref"}}
        run.error = "task failed"
        deps["run_repo"].save(run)
        task = _make_task(status=TaskStatus.FAILED)
        task.error = "something went wrong"
        deps["task_repo"].save(task)

        uc = RerunTaskUseCase(**deps)
        result = await uc.execute(RerunTaskCommand(run_id="run-1", task_id="task-1"))

        assert result.rerun is True
        assert result.previous_status == "FAILED"
        assert result.status == "RUNNING"
        assert "rerun" in result.idempotency_key

        # Task should be reset
        updated_task = deps["task_repo"].get_by_id("task-1")
        assert updated_task.status == TaskStatus.RUNNING
        assert updated_task.error is None
        assert updated_task.output_ref is None
        assert updated_task.completed_at is None

        # Run should be back to RUNNING
        updated_run = deps["run_repo"].get_by_id("run-1")
        assert updated_run.status == RunStatus.RUNNING
        assert updated_run.error is None

        # Node should be removed from completed_nodes
        assert "n1" not in updated_run.context.completed_nodes

        # Context variables for node should be cleared
        assert "n1" not in updated_run.context.variables

        # No explicit new_input → rerun re-resolves via the node-execution
        # path (so a node edit would take effect), NOT the stale-input
        # emit_node_dispatched shortcut.
        assert deps["orchestration_engine"].reexecute_calls == [("run-1", "task-1")]
        assert deps["orchestration_engine"].rerun_calls == []
        # resolved_input cleared so the engine re-resolves it.
        assert updated_task.resolved_input is None

    @pytest.mark.asyncio
    async def test_rerun_with_new_input(self):
        deps = _build_deps()
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)
        run = _make_run(completed_nodes=["n1"])
        deps["run_repo"].save(run)
        task = _make_task(status=TaskStatus.COMPLETED)
        deps["task_repo"].save(task)

        uc = RerunTaskUseCase(**deps)
        result = await uc.execute(RerunTaskCommand(
            run_id="run-1",
            task_id="task-1",
            new_input={"x": 999},
        ))

        assert result.rerun is True
        assert result.resolved_input == {"x": 999}

        updated_task = deps["task_repo"].get_by_id("task-1")
        assert updated_task.resolved_input == {"x": 999}

        # Explicit new_input is an override: dispatched directly (preserving
        # the caller input), NOT re-resolved via the node-execution path.
        spy = deps["orchestration_engine"]
        assert len(spy.rerun_calls) == 1
        assert spy.reexecute_calls == []

    @pytest.mark.asyncio
    async def test_rerun_warns_about_processed_successors(self):
        deps = _build_deps()
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)
        run = _make_run(completed_nodes=["n1", "n2"])
        deps["run_repo"].save(run)
        task = _make_task(status=TaskStatus.COMPLETED)
        deps["task_repo"].save(task)

        uc = RerunTaskUseCase(**deps)
        result = await uc.execute(RerunTaskCommand(run_id="run-1", task_id="task-1"))

        assert result.rerun is True
        assert "n2" in result.warning
        assert "already been processed" in result.warning

    @pytest.mark.asyncio
    async def test_rerun_without_new_input_reexecutes_node(self):
        """Bugs #5/#6: with no explicit new_input, rerun re-executes the node
        through the engine (re-resolving from the current definition) instead
        of replaying the stale resolved input."""
        deps = _build_deps()
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)
        run = _make_run()
        deps["run_repo"].save(run)
        task = _make_task(status=TaskStatus.FAILED)
        deps["task_repo"].save(task)

        spy = deps["orchestration_engine"]
        uc = RerunTaskUseCase(**deps)
        await uc.execute(RerunTaskCommand(run_id="run-1", task_id="task-1"))

        # Re-resolution path, not the stale-input emit_node_dispatched shortcut.
        assert spy.reexecute_calls == [("run-1", "task-1")]
        assert spy.dispatched_calls == []

    @pytest.mark.asyncio
    async def test_rerun_recaptures_snapshot_reflecting_live_edits(self):
        """Bugs #5/#6: rerun re-captures a fresh snapshot from the LIVE
        workflow, so a node edit (URL) made after the run started is
        reflected on rerun instead of replaying the pinned definition."""
        from app.layer4_frameworks.persistence.memory.memory_workflow_snapshot_repo import (
            MemoryWorkflowSnapshotRepository,
        )
        from app.layer2_application.definition.workflow_snapshot_loader import (
            capture_snapshot,
        )

        snapshot_repo = MemoryWorkflowSnapshotRepository()
        workflow_repo = MemRepo()

        # Original (pinned) definition the run started with: url=old.
        wf_v1 = Workflow(
            id="wf-1", name="WF",
            nodes=[WorkflowNode(id="n1", name="T", node_type=NodeType.TASK,
                                worker_type="w1", config={"url": "http://old"})],
            edges=[],
        )
        old_snapshot_id = capture_snapshot(wf_v1, snapshot_repo)

        # Live workflow edited after the run started: url=new.
        wf_v2 = Workflow(
            id="wf-1", name="WF",
            nodes=[WorkflowNode(id="n1", name="T", node_type=NodeType.TASK,
                                worker_type="w1", config={"url": "http://new"})],
            edges=[],
        )
        workflow_repo.save(wf_v2)

        run = _make_run(status=RunStatus.FAILED, completed_nodes=["n1"])
        run.workflow_snapshot_id = old_snapshot_id
        run_repo = MemRepo()
        run_repo.save(run)

        task_repo = MemRepo()
        task_repo.save(_make_task(status=TaskStatus.FAILED))

        engine = _make_engine(workflow_repo=workflow_repo)
        uc = RerunTaskUseCase(
            logger=DummyLogger(),
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            stage_recorder=DummyStageRecorder(),
            orchestration_engine=engine,
            workflow_snapshot_repo=snapshot_repo,
        )
        result = await uc.execute(RerunTaskCommand(run_id="run-1", task_id="task-1"))
        assert result.rerun is True

        updated_run = run_repo.get_by_id("run-1")
        # A fresh snapshot was captured (id changed) reflecting the edit.
        assert updated_run.workflow_snapshot_id != old_snapshot_id
        # Read back via the repo the use case actually holds — the
        # conftest snapshot shim swaps in the shared test repo, so the
        # locally-constructed ``snapshot_repo`` above may not be it.
        new_snap = uc._snapshot_repo.get_by_id(updated_run.workflow_snapshot_id)
        reflected = Workflow.from_snapshot_dict(new_snap.definition).get_node("n1")
        assert reflected.config.get("url") == "http://new"
        # And it re-executes (re-resolves) rather than replaying stale input.
        assert engine.reexecute_calls == [("run-1", "task-1")]

    @pytest.mark.asyncio
    async def test_rerun_completed_task_on_running_run(self):
        """Re-running a COMPLETED task on a still-RUNNING run should work."""
        deps = _build_deps()
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)
        run = _make_run(status=RunStatus.RUNNING, completed_nodes=["n1"])
        deps["run_repo"].save(run)
        task = _make_task(status=TaskStatus.COMPLETED)
        deps["task_repo"].save(task)

        uc = RerunTaskUseCase(**deps)
        result = await uc.execute(RerunTaskCommand(run_id="run-1", task_id="task-1"))

        assert result.rerun is True
        assert result.previous_status == "COMPLETED"

        # Run should stay RUNNING (not changed)
        updated_run = deps["run_repo"].get_by_id("run-1")
        assert updated_run.status == RunStatus.RUNNING

    @pytest.mark.asyncio
    async def test_rerun_restamps_task_generation_to_current_run_generation(self):
        """Live bug 2026-06-07 (docs/design/bug-validation-2026-06-07.md):
        ``RerunRunUseCase`` bumps ``run.generation`` before delegating here, but
        the re-dispatched task kept its old ``run_generation`` — so the
        generation guard rejected the rerun's OWN completion callback as
        TASK_SUPERSEDED (HTTP 409) and the run hung forever. The re-dispatched
        task must be re-stamped to the run's current generation."""
        from app.layer2_application.generation_guard import should_reject_callback

        deps = _build_deps()
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)
        # Simulate the state RerunRunUseCase leaves before delegating: generation
        # bumped on the run, but the existing task still stamped at the prior one.
        run = _make_run(status=RunStatus.RUNNING, completed_nodes=["n1"])
        run.generation = 1
        deps["run_repo"].save(run)
        task = _make_task(status=TaskStatus.COMPLETED)
        task.run_generation = 0
        deps["task_repo"].save(task)

        uc = RerunTaskUseCase(**deps)
        result = await uc.execute(RerunTaskCommand(run_id="run-1", task_id="task-1"))
        assert result.rerun is True

        updated_task = deps["task_repo"].get_by_id("task-1")
        updated_run = deps["run_repo"].get_by_id("run-1")
        # Re-stamped to the run's current generation ...
        assert updated_task.run_generation == updated_run.generation == 1
        # ... so the guard ACCEPTS the rerun's own completion callback (no 409 hang).
        assert should_reject_callback(
            updated_task, updated_run, deps["logger"], handler="complete",
        ) is False
