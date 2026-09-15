"""Tests for test_node and reset_test_run features."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.node_handlers.base import NodeHandlerRegistry, NodeHandlerResult
from app.layer2_application.features.test_node.use_cases.test_node_use_case import (
    TestNodeUseCase,
    TestNodeCommand,
)
from app.layer2_application.features.reset_test_run.use_cases.reset_test_run_use_case import (
    ResetTestRunUseCase,
    ResetTestRunCommand,
)

from tests.conftest import DummyLogger, DummyEventPublisher, DummyStageRecorder


# ── Helpers ──────────────────────────────────────────────────────


def _make_workflow(nodes=None, edges=None, test_run_id=None):
    return Workflow(
        id="wf-1",
        name="Test WF",
        nodes=nodes or [
            WorkflowNode(id="compute-1", name="Compute1", node_type=NodeType.COMPUTE,
                         config={"expression": "10 * 5", "result_key": "result"}),
            WorkflowNode(id="task-1", name="Task1", node_type=NodeType.TASK, worker_type="w1",
                         input_binding={"value": "{{$compute-1.output.result}}"}),
        ],
        edges=edges or [
            WorkflowEdge(source_node_id="compute-1", target_node_id="task-1"),
        ],
        test_run_id=test_run_id,
    )


def _make_handler_registry(handler_result=None):
    registry = MagicMock(spec=NodeHandlerRegistry)
    handler = AsyncMock()
    handler.handle = AsyncMock(return_value=handler_result or NodeHandlerResult())
    registry.get.return_value = handler
    return registry


def _make_engine():
    engine = MagicMock()
    engine.apply_state_updates = MagicMock()
    engine.apply_state_updates_as_files = AsyncMock()
    engine.publish_events = MagicMock()
    engine.publish_event = MagicMock()
    engine.drain_events = AsyncMock()
    engine.drain_events = AsyncMock()
    engine._queue = MagicMock()
    engine._queue.enqueue = AsyncMock()
    engine._start_child_workflow = AsyncMock()
    # ``_to_file_ref`` file-backs a single output value. With no
    # data_store_service wired (the real engine's behaviour, and this mock's)
    # it is a pass-through, so the stored value equals the raw value.
    engine._to_file_ref = AsyncMock(side_effect=lambda key, value: value)

    # Use the real implementation for apply_state_updates (static method).
    from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
    engine.apply_state_updates = OrchestrationEngine.apply_state_updates
    return engine


class MemRepo:
    """Minimal in-memory repo for tests."""

    def __init__(self):
        self._store = {}

    def save(self, entity):
        self._store[entity.id] = entity

    def get_by_id(self, id_):
        return self._store.get(id_)

    def list_by_run(self, run_id):
        return [e for e in self._store.values() if getattr(e, "run_id", None) == run_id]

    # Async wrappers — async-IO conversion routes use-case calls through
    # ``aget_by_id`` / ``asave`` / ``alist_by_run``. In-memory ops don't
    # benefit from executor offload; just delegate.
    async def aget_by_id(self, id_):
        return self.get_by_id(id_)

    async def asave(self, entity):
        self.save(entity)

    async def alist_by_run(self, run_id):
        return self.list_by_run(run_id)


# ── TestNodeUseCase ──────────────────────────────────────────────


class TestTestNode:

    @pytest.fixture
    def deps(self):
        wf_repo = MemRepo()
        run_repo = MemRepo()
        task_repo = MemRepo()
        from app.layer4_frameworks.persistence.memory.memory_workflow_snapshot_repo import (
            MemoryWorkflowSnapshotRepository,
        )
        return {
            "logger": DummyLogger(),
            "workflow_repo": wf_repo,
            "workflow_snapshot_repo": MemoryWorkflowSnapshotRepository(),
            "run_repo": run_repo,
            "task_repo": task_repo,
            "stage_recorder": DummyStageRecorder(),
            "event_publisher": DummyEventPublisher(),
        }

    @pytest.mark.asyncio
    async def test_workflow_not_found(self, deps):
        registry = _make_handler_registry()
        engine = _make_engine()
        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)

        result = await uc.execute(TestNodeCommand(workflow_id="missing", node_id="n1"))
        assert result.error_code == "WORKFLOW_NOT_FOUND"
        assert result.error_details.get("workflow_id") == "missing"

    @pytest.mark.asyncio
    async def test_node_not_found(self, deps):
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)
        registry = _make_handler_registry()
        engine = _make_engine()
        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)

        result = await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="missing"))
        assert result.error_code == "NODE_NOT_FOUND"
        assert result.error_details.get("node_id") == "missing"

    @pytest.mark.asyncio
    async def test_inline_builtin_node(self, deps):
        """Testing a COMPUTE node should execute inline and accumulate context."""
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)

        handler_result = NodeHandlerResult(
            state_updates={"compute-1": {"output": {"result": 50}}},
        )
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()
        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)

        result = await uc.execute(TestNodeCommand(
            workflow_id="wf-1",
            node_id="compute-1",
            input_data={"price": 10, "quantity": 5},
        ))

        assert result.error == ""
        assert result.execution_type == "inline"
        assert result.node_type == "COMPUTE"
        assert result.handler_output == {"output": {"result": 50}}
        assert result.run_id != ""

        # Verify test run was created and linked to workflow
        updated_wf = deps["workflow_repo"].get_by_id("wf-1")
        assert updated_wf.test_run_id == result.run_id

        # Verify context accumulated
        test_run = deps["run_repo"].get_by_id(result.run_id)
        assert test_run.is_test is True
        assert test_run.context.variables.get("input") == {"price": 10, "quantity": 5}
        assert "compute-1" in test_run.context.completed_nodes

    @pytest.mark.asyncio
    async def test_prompt_node_executes_inline(self, deps):
        """A PROMPT node is an inline (runtime="engine") node, so test_node
        must run it inline — not fall through to the worker-dispatch branch.

        Regression for the original bug: PROMPT was missing from the
        hand-listed BUILTIN_NODE_TYPES, so testing it did nothing (no
        COMPLETED task, no file-backed output). The set is now derived from
        the domain spec runtime, which classifies PROMPT as inline.
        """
        wf = _make_workflow(
            nodes=[
                WorkflowNode(id="prompt-1", name="Prompt1", node_type=NodeType.PROMPT,
                             config={"content": "Summarize this."}),
                WorkflowNode(id="task-1", name="Task1", node_type=NodeType.TASK,
                             worker_type="w1",
                             input_binding={"text": "{{$prompt-1.output.content}}"}),
            ],
            edges=[WorkflowEdge(source_node_id="prompt-1", target_node_id="task-1")],
        )
        deps["workflow_repo"].save(wf)

        handler_result = NodeHandlerResult(
            state_updates={"prompt-1": {"output": {"content": "Summarize this."}}},
        )
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()
        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)

        result = await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="prompt-1"))

        assert result.error == ""
        assert result.execution_type == "inline"
        assert result.node_type == "PROMPT"
        assert result.handler_output == {"output": {"content": "Summarize this."}}

        # A COMPLETED task is left for the canvas, and the node is recorded done.
        test_run = deps["run_repo"].get_by_id(result.run_id)
        assert "prompt-1" in test_run.context.completed_nodes
        prompt_tasks = [
            t for t in deps["task_repo"].list_by_run(result.run_id)
            if t.node_id == "prompt-1"
        ]
        assert prompt_tasks and prompt_tasks[0].status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_reuses_existing_test_run(self, deps):
        """Second test call should reuse the same test run."""
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)

        handler_result = NodeHandlerResult(
            state_updates={"compute-1": {"output": {"result": 50}}},
        )
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()
        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)

        r1 = await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="compute-1"))
        r2 = await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="compute-1"))

        assert r1.run_id == r2.run_id

    @pytest.mark.asyncio
    async def test_reuses_same_run_regardless_of_status(self, deps):
        """test_run_id is permanent — always reused regardless of status."""
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)

        # Pre-create a test run (any status)
        old_run = WorkflowRun(
            id="old-test-run",
            workflow_id="wf-1",
            status=RunStatus.COMPLETED,
            is_test=True,
        )
        deps["run_repo"].save(old_run)
        wf.test_run_id = "old-test-run"
        deps["workflow_repo"].save(wf)

        registry = _make_handler_registry(NodeHandlerResult(
            state_updates={"compute-1": {"output": {"result": 1}}},
        ))
        engine = _make_engine()
        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)

        result = await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="compute-1"))

        # Same run ID — never changes
        assert result.run_id == "old-test-run"
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_dispatched_task_node(self, deps):
        """Testing a TASK node should dispatch to worker."""
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)

        task = Task(id="t-1", run_id="", node_id="task-1", worker_type="w1",
                    resolved_input={"value": 50})
        handler_result = NodeHandlerResult(tasks_to_dispatch=[task])
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()
        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)

        result = await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="task-1"))

        assert result.execution_type == "dispatched"
        assert result.task_id == "t-1"
        assert result.resolved_input == {"value": 50}
        assert result.worker_type == "w1"
        # Queue-mode: the use case enqueues an ExecuteNodeAction that the
        # worker will pick up for CSV conversion + actual dispatch. The
        # test run has is_test=True, so CompleteTaskUseCase skips
        # successor processing when the worker responds.
        engine._queue.enqueue.assert_called_once()

    @pytest.mark.asyncio
    async def test_pending_input_node(self, deps):
        """Testing an INPUT node should return pending_input."""
        wf = Workflow(
            id="wf-1", name="Test",
            nodes=[
                WorkflowNode(id="input-1", name="UserInput", node_type=NodeType.INPUT,
                             config={"input_schema": [{"key": "name", "type": "string"}],
                                     "instruction": "Enter your name"}),
            ],
            edges=[],
        )
        deps["workflow_repo"].save(wf)

        pending_task = Task(id="pt-1", run_id="", node_id="input-1",
                            status=TaskStatus.PENDING,
                            resolved_input={"name": "default"})
        handler_result = NodeHandlerResult(tasks_to_dispatch=[pending_task])
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()
        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)

        result = await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="input-1"))

        assert result.execution_type == "pending_input"
        assert result.input_schema == [{"key": "name", "type": "string"}]
        assert result.instruction == "Enter your name"
        assert result.prefilled_data == {"name": "default"}

    @pytest.mark.asyncio
    async def test_mock_variables_override(self, deps):
        """Mock variables should override accumulated context."""
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)

        # First call builds context
        handler_result1 = NodeHandlerResult(
            state_updates={"compute-1": {"output": {"result": 50}}},
        )
        registry = _make_handler_registry(handler_result1)
        engine = _make_engine()
        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)

        await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="compute-1"))

        # Second call with mock override
        task = Task(id="t-1", run_id="", node_id="task-1", worker_type="w1",
                    resolved_input={"value": 999})
        handler_result2 = NodeHandlerResult(tasks_to_dispatch=[task])
        registry2 = _make_handler_registry(handler_result2)
        uc2 = TestNodeUseCase(handler_registry=registry2, orchestration_engine=engine, **deps)

        result = await uc2.execute(TestNodeCommand(
            workflow_id="wf-1",
            node_id="task-1",
            mock_variables={"compute-1": {"output": {"result": 999}}},
        ))

        # Verify mock override was applied to context
        test_run = deps["run_repo"].get_by_id(result.run_id)
        assert test_run.context.variables["compute-1"] == {"output": {"result": 999}}

    @pytest.mark.asyncio
    async def test_workflow_node_starts_child_run(self, deps):
        """Testing a WORKFLOW node ACTUALLY runs the child sub-workflow (no
        longer a stub): the tracking task is marked RUNNING and
        ``_start_child_workflow`` is invoked. The node is NOT auto-completed —
        it completes when the child finishes (via ``complete_parent``)."""
        wf = Workflow(
            id="wf-1", name="Test",
            nodes=[
                WorkflowNode(id="wf-node-1", name="SubWF", node_type=NodeType.WORKFLOW,
                             config={"workflow_id": "child-wf", "on_failure": "skip"}),
            ],
            edges=[],
        )
        deps["workflow_repo"].save(wf)

        tracking = Task(id="tt-1", run_id="", node_id="wf-node-1",
                        status=TaskStatus.PENDING)
        handler_result = NodeHandlerResult(
            state_updates={"wf-node-1": {
                "metadata": {
                    "child_workflow_id": "child-wf",
                    "child_input": {"x": 1},
                    "on_failure": "skip",
                },
            }},
            requires_child_run=True,
            completion_mode="child_run",
            tasks_to_dispatch=[tracking],
        )
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()
        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)

        result = await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="wf-node-1"))

        assert result.execution_type == "child_run"
        assert result.handler_output["child_workflow_id"] == "child-wf"
        # The child sub-workflow was actually started.
        engine._start_child_workflow.assert_awaited_once()
        started_node = engine._start_child_workflow.await_args.args[0]
        started_task = engine._start_child_workflow.await_args.args[1]
        assert started_node.id == "wf-node-1"
        assert started_task is tracking
        # Tracking task saved RUNNING (canvas shows the node RUNNING), NOT COMPLETED.
        saved = deps["task_repo"].get_by_id("tt-1")
        assert saved.status == TaskStatus.RUNNING
        # The WORKFLOW node is NOT auto-completed and NOT marked done inline.
        run = deps["run_repo"].get_by_id(result.run_id)
        assert "wf-node-1" not in run.context.completed_nodes
        engine.emit_node_completed.assert_not_called()

    @pytest.mark.asyncio
    async def test_loop_node_enqueues_body_tasks(self, deps):
        """Testing a LOOP node enqueues its body entry tasks (iterations run via
        the worker queue); the LOOP node is not auto-completed."""
        wf = Workflow(
            id="wf-1", name="Test",
            nodes=[
                WorkflowNode(id="loop-1", name="Loop", node_type=NodeType.LOOP,
                             config={}),
            ],
            edges=[],
        )
        deps["workflow_repo"].save(wf)

        body = Task(id="body-1", run_id="", node_id="body-node",
                    status=TaskStatus.PENDING)
        handler_result = NodeHandlerResult(
            state_updates={"loop-1": {"metadata": {"total": 2}}},
            completion_mode="loop_body",
            tasks_to_dispatch=[body],
        )
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()
        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)

        result = await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="loop-1"))

        assert result.execution_type == "loop_body"
        # Body entry task enqueued for execution.
        engine._queue.enqueue.assert_called_once()
        action = engine._queue.enqueue.call_args.args[0]
        assert action.task_id == "body-1"
        assert deps["task_repo"].get_by_id("body-1").run_id == result.run_id
        # LOOP node NOT auto-completed.
        run = deps["run_repo"].get_by_id(result.run_id)
        assert "loop-1" not in run.context.completed_nodes
        engine.emit_node_completed.assert_not_called()

    @pytest.mark.asyncio
    async def test_workflow_retest_bumps_generation_and_cancels_prior_child(self, deps):
        """Re-testing a WORKFLOW node bumps generation (fresh deterministic
        child id) and cancels the prior child run + its tasks so a paused prior
        child can't be resolved onto the new tracking task."""
        wf = Workflow(
            id="wf-1", name="Test",
            nodes=[
                WorkflowNode(id="wf-node-1", name="SubWF", node_type=NodeType.WORKFLOW,
                             config={"workflow_id": "child-wf"}),
            ],
            edges=[], test_run_id="trun-1",
        )
        deps["workflow_repo"].save(wf)

        # Existing permanent test run with a prior child (paused / RUNNING).
        test_run = WorkflowRun(id="trun-1", workflow_id="wf-1",
                               status=RunStatus.RUNNING, is_test=True, generation=3)
        test_run.context.variables = {"input": {}, "artifacts": {}}
        test_run.context.child_runs = {"wf-node-1": "old-child"}
        deps["run_repo"].save(test_run)
        prior_child = WorkflowRun(id="old-child", workflow_id="child-wf",
                                  status=RunStatus.RUNNING)
        deps["run_repo"].save(prior_child)
        prior_child_task = Task(id="oct-1", run_id="old-child", node_id="c1",
                                status=TaskStatus.RUNNING)
        deps["task_repo"].save(prior_child_task)

        tracking = Task(id="tt-1", run_id="", node_id="wf-node-1",
                        status=TaskStatus.PENDING)
        handler_result = NodeHandlerResult(
            state_updates={"wf-node-1": {"metadata": {
                "child_workflow_id": "child-wf", "child_input": {}, "on_failure": "fail"}}},
            requires_child_run=True, completion_mode="child_run",
            tasks_to_dispatch=[tracking],
        )
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()
        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)

        await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="wf-node-1"))

        run = deps["run_repo"].get_by_id("trun-1")
        assert run.generation == 4  # bumped from 3
        # Prior child run + its tasks cancelled; entry popped before re-register.
        assert deps["run_repo"].get_by_id("old-child").status == RunStatus.CANCELLED
        assert deps["task_repo"].get_by_id("oct-1").status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_reuse_surgical_reset_preserves_upstream(self, deps):
        """Re-testing a middle node clears it + downstream, keeps upstream,
        rewinds the projector, and re-seeds RUNNING + upstream variables.

        Regression for the deployed bug where ``/node/test`` on a
        HUMAN_ACTION ran against a test_run whose projector still said
        ``status=COMPLETED`` (POST /submit → 409) and whose
        ``view.variables[node_id]`` pointed at a GC'd file_id from a
        prior call (PATCH /data → factory 404).
        """
        # 3-node chain: upstream → target → downstream.
        wf = _make_workflow(
            nodes=[
                WorkflowNode(id="upstream", name="Upstream", node_type=NodeType.COMPUTE,
                             config={"expression": "1+1", "result_key": "sum"}),
                WorkflowNode(id="target", name="Target", node_type=NodeType.HUMAN_ACTION,
                             config={"instruction": "Confirm"}),
                WorkflowNode(id="downstream", name="Downstream", node_type=NodeType.COMPUTE,
                             config={"expression": "2+2", "result_key": "sum"}),
            ],
            edges=[
                WorkflowEdge(source_node_id="upstream", target_node_id="target"),
                WorkflowEdge(source_node_id="target", target_node_id="downstream"),
            ],
        )
        deps["workflow_repo"].save(wf)

        # Pre-existing test_run: prior cycle ran to COMPLETED with stale
        # entries for every node + a stale PENDING task on the target.
        prior = WorkflowRun(
            id="run-1", workflow_id="wf-1",
            status=RunStatus.COMPLETED, is_test=True,
        )
        prior.context.variables = {
            "input": {"x": 1},
            "upstream": {"output": {"sum": 2}},
            "target": {"file_id": "stale-target-file", "artifact_type": "object_field"},
            "downstream": {"output": {"sum": 4}},
        }
        prior.context.completed_nodes = {"upstream", "target", "downstream"}
        prior.context.last_output_ports = {"upstream": "success", "target": "confirm"}
        prior.context.last_taken_edge_ports = {"upstream": {"e1": "success"}}
        deps["run_repo"].save(prior)
        wf.test_run_id = "run-1"
        deps["workflow_repo"].save(wf)

        # Stale tasks on target + downstream from the prior cycle.
        stale_target = Task(id="run-1-target", run_id="run-1", node_id="target",
                            status=TaskStatus.PENDING)
        stale_downstream = Task(id="run-1-downstream", run_id="run-1",
                                node_id="downstream", status=TaskStatus.COMPLETED)
        deps["task_repo"].save(stale_target)
        deps["task_repo"].save(stale_downstream)

        # Spy stubs for the projector-rewind side effects.
        cleared: list[str] = []
        snapshot_deletes: list[tuple[str, int]] = []
        invalidated: list[str] = []

        class _LogRepo:
            def append(self, env): return True
            def list_by_run(self, rid): return []
            def list_by_run_since(self, rid, seq): return []
            def clear(self, rid):
                cleared.append(rid)
                return 0

        class _SnapRepo:
            def save(self, snap): pass
            def load_latest(self, rid, *, min_schema_version): return None
            def delete_older_than(self, rid, keep_sequence):
                snapshot_deletes.append((rid, keep_sequence))
                return 0

        class _ViewStore:
            def get(self, rid): return None
            def invalidate(self, rid): invalidated.append(rid)

        # Handler for the target HUMAN_ACTION — returns a fresh task with
        # a *new* file_id, exactly like the production handler would after
        # provisioning a working CSV via the file_client.
        fresh_task = Task(
            id="run-1-target", run_id="run-1", node_id="target",
            status=TaskStatus.PENDING,
            resolved_input={"file_id": "fresh-target-file",
                            "artifact_type": "object_field"},
        )
        handler_result = NodeHandlerResult(
            tasks_to_dispatch=[fresh_task],
            state_updates={"target": {"file_id": "fresh-target-file",
                                       "artifact_type": "object_field"}},
        )
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()

        uc = TestNodeUseCase(
            handler_registry=registry,
            orchestration_engine=engine,
            event_log_repo=_LogRepo(),
            run_context_view_store=_ViewStore(),
            snapshot_repo=_SnapRepo(),
            **deps,
        )

        result = await uc.execute(
            TestNodeCommand(workflow_id="wf-1", node_id="target"),
        )

        # --- Aggregate-side surgical reset ----------------------------
        run = deps["run_repo"].get_by_id("run-1")
        # Upstream PRESERVED — handler needs it for variable resolution.
        assert run.context.variables["upstream"] == {"output": {"sum": 2}}
        assert "upstream" in run.context.completed_nodes
        assert run.context.last_output_ports.get("upstream") == "success"
        # Downstream POPPED — stale entries must not leak through.
        assert "downstream" not in run.context.variables
        assert "downstream" not in run.context.completed_nodes
        # Target itself — the handler wrote a fresh entry, so the post-
        # handler aggregate has the new file_id, not the stale one.
        assert run.context.variables["target"]["file_id"] == "fresh-target-file"
        # Aggregate status flipped back to RUNNING.
        assert run.status == RunStatus.RUNNING

        # --- Stale tasks cancelled -----------------------------------
        # The handler upserted ``run-1-target`` to PENDING after we
        # cancelled it. The cancellation happened *before* the upsert,
        # so by end-of-call the task is PENDING (fresh) — but the
        # downstream task should be CANCELLED.
        assert deps["task_repo"].get_by_id("run-1-downstream").status == TaskStatus.CANCELLED

        # --- Projector rewind ----------------------------------------
        assert cleared == ["run-1"]
        assert snapshot_deletes and snapshot_deletes[0][0] == "run-1"
        assert invalidated == ["run-1"]

        # --- Lifecycle re-seed ---------------------------------------
        engine.emit_run_status.assert_called_once()
        status_call = engine.emit_run_status.call_args
        assert status_call.args[1] == "started"
        # The reseed emits one bootstrap ``variables_seeded`` (no ``seed_id``);
        # the BUILTIN/PENDING branch then emits a per-node ``node_output:*``
        # seed to mirror the handler output into the view. Assert on the
        # bootstrap one here.
        bootstrap_calls = [
            c for c in engine.emit_variables_seeded.call_args_list
            if "seed_id" not in c.kwargs
        ]
        assert len(bootstrap_calls) == 1
        seeded = bootstrap_calls[0].args[1]
        # Upstream survives the reseed; downstream does not.
        assert "upstream" in seeded
        assert "downstream" not in seeded
        # Target was popped before reseed too, since handler writes go
        # to the aggregate AFTER the reseed call. Confirms the order.
        assert "target" not in seeded

        assert result.execution_type == "pending_input"
        assert result.task_id == "run-1-target"

    @pytest.mark.asyncio
    async def test_reuse_hoists_task_upstream_from_view(self, deps):
        """Phase 3b.1: TASK outputs land only in the projector view, never
        the aggregate. Without the hoist, ``_reseed_projector`` would
        re-emit ``VariablesSeeded`` with only ``{artifacts, input}`` and
        the next handler's input resolution would fail with
        ``"Source '<upstream_task>' not found in context"``.

        This test simulates a re-test of a downstream node whose upstream
        is a TASK already in the view. The hoist must copy the upstream's
        output into the aggregate so the reseed carries it forward.
        """
        wf = _make_workflow(
            nodes=[
                WorkflowNode(id="upstream-task", name="UpstreamTask",
                             node_type=NodeType.TASK, worker_type="w1"),
                WorkflowNode(id="downstream", name="Downstream",
                             node_type=NodeType.COMPUTE,
                             config={"expression": "1+1", "result_key": "x"}),
            ],
            edges=[WorkflowEdge(
                source_node_id="upstream-task", target_node_id="downstream",
            )],
        )
        deps["workflow_repo"].save(wf)

        # Pre-existing test_run. Aggregate has only the bootstrap entries
        # (artifacts, input) — the upstream TASK's output exists only on
        # the projector view, mirroring Phase 3b.1 semantics.
        prior = WorkflowRun(
            id="run-1", workflow_id="wf-1",
            status=RunStatus.COMPLETED, is_test=True,
        )
        prior.context.variables = {"artifacts": {}, "input": {}}
        prior.context.completed_nodes = {"upstream-task", "downstream"}
        deps["run_repo"].save(prior)
        wf.test_run_id = "run-1"
        deps["workflow_repo"].save(wf)

        class _ViewStore:
            def get(self, rid):
                # The view carries the TASK upstream's output (file_ref)
                # that the projector built from NodeOutputProduced.
                view = MagicMock()
                view.variables = {
                    "upstream-task": {
                        "__file_ref": True,
                        "file_id": "upstream-out-123",
                        "artifact_type": "object_field",
                    },
                    # Downstream entry in the view will be dropped by the
                    # surgical-reset pop, so the hoist+pop combo must
                    # exclude it from the emitted reseed payload.
                    "downstream": {"output": {"x": 2}},
                }
                return view

            def invalidate(self, rid): pass

        class _LogRepo:
            def append(self, env): return True
            def list_by_run(self, rid): return []
            def list_by_run_since(self, rid, seq): return []
            def clear(self, rid): return 0

        class _SnapRepo:
            def save(self, snap): pass
            def load_latest(self, rid, *, min_schema_version): return None
            def delete_older_than(self, rid, keep_sequence): return 0

        # Downstream COMPUTE handler — production handler would resolve
        # ``$.upstream-task.file_id`` against ``run.context.variables``.
        # The test just verifies aggregate is populated when handler runs.
        handler_result = NodeHandlerResult(
            state_updates={"downstream": {"output": {"x": 2}}},
        )
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()

        uc = TestNodeUseCase(
            handler_registry=registry,
            orchestration_engine=engine,
            event_log_repo=_LogRepo(),
            run_context_view_store=_ViewStore(),
            snapshot_repo=_SnapRepo(),
            **deps,
        )

        await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="downstream"))

        # The upstream TASK's file_ref must have been hoisted into the
        # aggregate before reseed, so the projector's new VariablesSeeded
        # event carries it forward.
        # The reseed emits one bootstrap ``variables_seeded`` (no ``seed_id``);
        # the BUILTIN/PENDING branch then emits a per-node ``node_output:*``
        # seed to mirror the handler output into the view. Assert on the
        # bootstrap one here.
        bootstrap_calls = [
            c for c in engine.emit_variables_seeded.call_args_list
            if "seed_id" not in c.kwargs
        ]
        assert len(bootstrap_calls) == 1
        seeded = bootstrap_calls[0].args[1]
        assert "upstream-task" in seeded, (
            f"upstream TASK output dropped from reseed; "
            f"available keys: {list(seeded.keys())}"
        )
        assert seeded["upstream-task"]["file_id"] == "upstream-out-123"
        # Downstream was popped before reseed.
        assert "downstream" not in seeded

        # Aggregate also has the hoisted entry so the handler call (which
        # resolves $.upstream-task.* against run.context.variables) sees it.
        run = deps["run_repo"].get_by_id("run-1")
        assert run.context.variables["upstream-task"]["file_id"] == "upstream-out-123"

    @pytest.mark.asyncio
    async def test_reuse_hoist_view_wins_over_stale_aggregate_output(self, deps):
        """Data-loss regression ("upstream node's data gone").

        A submitted upstream node's real output lives only on the projector
        view (Phase 3b.1: ``apply_state_updates_as_files`` is event-only),
        while the aggregate still holds the empty dispatch-time provisioned
        ref that an earlier ``test_node`` PENDING call wrote directly. When a
        downstream node is re-tested, the hoist must let the *view* win for
        that node-output key — a gap-fill (the old behaviour) would keep the
        stale ref and the upstream node's data vanishes on the next call.

        Metadata keys (``artifacts``) stay aggregate-authoritative:
        ``register_artifacts`` mutates the aggregate, so a staler view copy
        must NOT clobber it.
        """
        wf = _make_workflow(
            nodes=[
                WorkflowNode(id="upstream-input", name="UpstreamInput",
                             node_type=NodeType.INPUT,
                             config={"input_schema": [], "instruction": ""}),
                WorkflowNode(id="downstream", name="Downstream",
                             node_type=NodeType.COMPUTE,
                             config={"expression": "1+1", "result_key": "x"}),
            ],
            edges=[WorkflowEdge(
                source_node_id="upstream-input", target_node_id="downstream",
            )],
        )
        deps["workflow_repo"].save(wf)

        # Aggregate carries the STALE provisioned ref for the upstream INPUT
        # plus a *fresher* $artifacts registry (register_artifacts mutates
        # the aggregate, not an event).
        prior = WorkflowRun(
            id="run-1", workflow_id="wf-1",
            status=RunStatus.COMPLETED, is_test=True,
        )
        prior.context.variables = {
            "input": {},
            "artifacts": {"reg": "kept-from-aggregate"},
            "upstream-input": {"__file_ref": True, "file_id": "stale-provisioned"},
        }
        prior.context.completed_nodes = {"upstream-input", "downstream"}
        deps["run_repo"].save(prior)
        wf.test_run_id = "run-1"
        deps["workflow_repo"].save(wf)

        class _ViewStore:
            def get(self, rid):
                view = MagicMock()
                view.variables = {
                    # The real submitted output — only on the view.
                    "upstream-input": {"__file_ref": True,
                                       "file_id": "fresh-submitted"},
                    # A staler registry copy that must NOT win.
                    "artifacts": {"reg": "stale-from-view"},
                }
                return view

            def invalidate(self, rid): pass

        class _LogRepo:
            def append(self, env): return True
            def list_by_run(self, rid): return []
            def list_by_run_since(self, rid, seq): return []
            def clear(self, rid): return 0

        class _SnapRepo:
            def save(self, snap): pass
            def load_latest(self, rid, *, min_schema_version): return None
            def delete_older_than(self, rid, keep_sequence): return 0

        handler_result = NodeHandlerResult(
            state_updates={"downstream": {"output": {"x": 2}}},
        )
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()

        uc = TestNodeUseCase(
            handler_registry=registry,
            orchestration_engine=engine,
            event_log_repo=_LogRepo(),
            run_context_view_store=_ViewStore(),
            snapshot_repo=_SnapRepo(),
            **deps,
        )

        await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="downstream"))

        # The reseed emits one bootstrap ``variables_seeded`` (no ``seed_id``);
        # the BUILTIN/PENDING branch then emits a per-node ``node_output:*``
        # seed to mirror the handler output into the view. Assert on the
        # bootstrap one here.
        bootstrap_calls = [
            c for c in engine.emit_variables_seeded.call_args_list
            if "seed_id" not in c.kwargs
        ]
        assert len(bootstrap_calls) == 1
        seeded = bootstrap_calls[0].args[1]
        # View WON for the upstream node-output key — the data-loss fix.
        assert seeded["upstream-input"]["file_id"] == "fresh-submitted"
        # Metadata gap-filled — aggregate registry preserved, not clobbered.
        assert seeded["artifacts"] == {"reg": "kept-from-aggregate"}

        # Aggregate mirrors the same so handler input resolution sees fresh data.
        run = deps["run_repo"].get_by_id("run-1")
        assert run.context.variables["upstream-input"]["file_id"] == "fresh-submitted"

    @pytest.mark.asyncio
    async def test_reuse_reseed_restores_terminal_node_status(self, deps):
        """Status regression ("upper node back to PENDING").

        The projector rebuilds ``completed_nodes`` / ``skipped_nodes`` only
        from ``NodeCompleted`` / ``NodeCancelled`` events, which the
        log-clear in ``_reseed_projector`` destroys. The reseed must re-emit
        them for the surviving (upstream) nodes — otherwise the preserved
        node loses its terminal status in the view and the re-broadcast
        RunStarted resets the canvas to PENDING. The popped target/downstream
        nodes must NOT be re-emitted.
        """
        wf = _make_workflow(
            nodes=[
                WorkflowNode(id="upstream", name="Upstream",
                             node_type=NodeType.COMPUTE,
                             config={"expression": "1+1", "result_key": "x"}),
                WorkflowNode(id="downstream", name="Downstream",
                             node_type=NodeType.COMPUTE,
                             config={"expression": "2+2", "result_key": "y"}),
                WorkflowNode(id="skipped-branch", name="SkippedBranch",
                             node_type=NodeType.COMPUTE,
                             config={"expression": "3+3", "result_key": "z"}),
            ],
            edges=[WorkflowEdge(
                source_node_id="upstream", target_node_id="downstream",
            )],
        )
        deps["workflow_repo"].save(wf)

        prior = WorkflowRun(
            id="run-1", workflow_id="wf-1",
            status=RunStatus.COMPLETED, is_test=True,
        )
        prior.context.variables = {"input": {}, "artifacts": {}}
        prior.context.completed_nodes = {"upstream", "downstream"}
        prior.context.skipped_nodes = {"skipped-branch"}
        deps["run_repo"].save(prior)
        wf.test_run_id = "run-1"
        deps["workflow_repo"].save(wf)

        class _ViewStore:
            def get(self, rid): return None
            def invalidate(self, rid): pass

        class _LogRepo:
            def append(self, env): return True
            def list_by_run(self, rid): return []
            def list_by_run_since(self, rid, seq): return []
            def clear(self, rid): return 0

        class _SnapRepo:
            def save(self, snap): pass
            def load_latest(self, rid, *, min_schema_version): return None
            def delete_older_than(self, rid, keep_sequence): return 0

        handler_result = NodeHandlerResult(
            state_updates={"downstream": {"output": {"y": 4}}},
        )
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()

        uc = TestNodeUseCase(
            handler_registry=registry,
            orchestration_engine=engine,
            event_log_repo=_LogRepo(),
            run_context_view_store=_ViewStore(),
            snapshot_repo=_SnapRepo(),
            **deps,
        )

        await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="downstream"))

        # The reseed re-emits the preserved upstream node's COMPLETED status
        # (first call); the tested ``downstream`` node is then emitted once at
        # the end as its OWN completion — the live-canvas signal that flips it
        # green without a refresh — not by the reseed. Order matters: upstream
        # (reseed-restored) precedes downstream (post-execution).
        completed = [
            c.args[2].id for c in engine.emit_node_completed.call_args_list
            if c.args[2] is not None
        ]
        assert completed == ["upstream", "downstream"]
        # Preserved skipped node re-emitted as a skip.
        engine.emit_node_cancelled.assert_called_once()
        cancel_kwargs = engine.emit_node_cancelled.call_args.kwargs
        assert cancel_kwargs.get("node_id") == "skipped-branch"
        assert cancel_kwargs.get("reason") == "skipped"

    @pytest.mark.asyncio
    async def test_inline_builtin_output_synced_to_view(self, deps):
        """BUILTIN output-to-view gap: the inline handler output must be
        mirrored into the projector view via a fan-out-free per-node
        ``node_output:*`` seed (``emit_variables_seeded`` with ``__bootstrap__``
        source), so GET /data / view-store readers see it. The aggregate
        write alone is invisible to the event-sourced view.
        """
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)

        handler_result = NodeHandlerResult(
            state_updates={"compute-1": {"output": {"result": 50}}},
        )
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()
        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)

        await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="compute-1"))

        node_output_calls = [
            c for c in engine.emit_variables_seeded.call_args_list
            if c.kwargs.get("seed_id") == "node_output:compute-1"
        ]
        assert len(node_output_calls) == 1, (
            "BUILTIN inline output was not mirrored to the view via a "
            "per-node seed"
        )
        assert node_output_calls[0].args[1] == {"compute-1": {"output": {"result": 50}}}

    @pytest.mark.asyncio
    async def test_inline_builtin_output_is_file_backed_for_artifact_id(self, deps):
        """A tested BUILTIN node's output must be file-backed exactly like a
        real run, so ``GET /data?side=output`` exposes a queryable file_ref
        (file_id / artifact_id) rather than a raw inline dict.

        Regression: test_node wrote BUILTIN state_updates straight to the
        aggregate/view as raw dicts, bypassing the file-backing a real run
        does via ``apply_state_updates_as_files`` → ``_to_file_ref``. A raw
        dict is not a file_ref, so ``build_descriptor_tree`` returned None and
        the output side carried no artifact_id.
        """
        from app.layer1_domain.entities.data_reference import (
            build_file_ref,
            is_file_ref,
        )

        wf = _make_workflow()
        deps["workflow_repo"].save(wf)

        handler_result = NodeHandlerResult(
            state_updates={"compute-1": {"output": {"result": 50}}},
        )
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()
        # Simulate a wired data_store_service: ``_to_file_ref`` uploads the
        # value and returns a file_ref pointer (file_id + root artifact_id).
        file_ref = build_file_ref(file_id="f_compute1", row_count=1, columns=["output"])
        engine._to_file_ref = AsyncMock(side_effect=lambda key, value: file_ref)

        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)
        result = await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="compute-1"))

        assert result.execution_type == "inline"
        # Aggregate carries the file_ref pointer (not the raw dict).
        test_run = deps["run_repo"].get_by_id(result.run_id)
        stored = test_run.context.variables["compute-1"]
        assert is_file_ref(stored)
        assert stored["file_id"] == "f_compute1"
        assert stored["artifact_id"]  # __root__ — what GET /data surfaces
        # The view mirror (what GET /data?side=output rebuilds from) gets the
        # file_ref too, so ``build_descriptor_tree`` yields an artifact_id.
        node_output_calls = [
            c for c in engine.emit_variables_seeded.call_args_list
            if c.kwargs.get("seed_id") == "node_output:compute-1"
        ]
        assert len(node_output_calls) == 1
        assert is_file_ref(node_output_calls[0].args[1]["compute-1"])
        # Raw resolved output is still returned for immediate test feedback.
        assert result.handler_output == {"output": {"result": 50}}

    @pytest.mark.asyncio
    async def test_builtin_leaves_completed_task_for_canvas_status(self, deps):
        """A tested BUILTIN node must leave a COMPLETED task so the canvas
        status endpoints render it COMPLETED — not stuck red.

        ``aggregate_node_status`` (list_run_node_statuses / get_node_execution,
        what the canvas reads) derives a node's status from its TASKS +
        skipped_nodes, NOT the projector view. Before the fix a BUILTIN node
        had no task, so it showed ``NOT_STARTED`` (or, if a prior run's stale
        task had been cancelled by the surgical reset, ``CANCELLED`` / red)
        even though the test ran it.
        """
        from app.layer2_application.read.node_status_aggregator import (
            aggregate_node_status,
        )

        wf = _make_workflow()
        deps["workflow_repo"].save(wf)
        handler_result = NodeHandlerResult(
            state_updates={"compute-1": {"output": {"result": 50}}},
        )
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()
        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)

        result = await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="compute-1"))

        assert result.execution_type == "inline"
        task = deps["task_repo"].get_by_id(f"{result.run_id}-compute-1")
        assert task is not None
        assert task.status == TaskStatus.COMPLETED
        # End-to-end: the derived canvas status is COMPLETED, not red.
        run = deps["run_repo"].get_by_id(result.run_id)
        assert aggregate_node_status(
            "compute-1", [task], run.context.skipped_nodes, run.status,
        ) == "COMPLETED"

    @pytest.mark.asyncio
    async def test_pending_provisioned_ref_synced_to_view(self, deps):
        """PENDING output-to-view gap: the freshly-provisioned working ref
        must be mirrored into the view fan-out-free so GET /data?side=output
        surfaces it after a (re-)dispatch — without signalling successors
        (the form isn't submitted yet)."""
        wf = Workflow(
            id="wf-1", name="Test",
            nodes=[
                WorkflowNode(id="input-1", name="UserInput", node_type=NodeType.INPUT,
                             config={"input_schema": [], "instruction": ""}),
            ],
            edges=[],
        )
        deps["workflow_repo"].save(wf)

        provisioned = {"__file_ref": True, "file_id": "provisioned-1"}
        pending_task = Task(id="pt-1", run_id="", node_id="input-1",
                            status=TaskStatus.PENDING, resolved_input=provisioned)
        handler_result = NodeHandlerResult(
            tasks_to_dispatch=[pending_task],
            state_updates={"input-1": provisioned},
        )
        registry = _make_handler_registry(handler_result)
        engine = _make_engine()
        uc = TestNodeUseCase(handler_registry=registry, orchestration_engine=engine, **deps)

        await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="input-1"))

        node_output_calls = [
            c for c in engine.emit_variables_seeded.call_args_list
            if c.kwargs.get("seed_id") == "node_output:input-1"
        ]
        assert len(node_output_calls) == 1
        assert node_output_calls[0].args[1] == {"input-1": provisioned}


# ── ResetTestRunUseCase ──────────────────────────────────────────


class TestResetTestRun:

    @pytest.fixture
    def deps(self):
        return {
            "logger": DummyLogger(),
            "workflow_repo": MemRepo(),
            "run_repo": MemRepo(),
        }

    @pytest.mark.asyncio
    async def test_workflow_not_found(self, deps):
        uc = ResetTestRunUseCase(**deps)
        result = await uc.execute(ResetTestRunCommand(workflow_id="missing"))
        assert result.error_code == "WORKFLOW_NOT_FOUND"
        assert result.error_details.get("workflow_id") == "missing"

    @pytest.mark.asyncio
    async def test_reset_creates_run_if_none(self, deps):
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)

        uc = ResetTestRunUseCase(**deps)
        result = await uc.execute(ResetTestRunCommand(workflow_id="wf-1"))

        assert result.error == ""
        assert result.run_id != ""
        assert result.workflow_id == "wf-1"

        # Verify workflow has test_run_id
        updated_wf = deps["workflow_repo"].get_by_id("wf-1")
        assert updated_wf.test_run_id == result.run_id

        # Verify run is test run
        run = deps["run_repo"].get_by_id(result.run_id)
        assert run.is_test is True
        assert run.status == RunStatus.CREATED

    @pytest.mark.asyncio
    async def test_reset_clears_state_keeps_same_id(self, deps):
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)

        # Create an existing test run with state
        old_run = WorkflowRun(
            id="old-run", workflow_id="wf-1",
            status=RunStatus.RUNNING, is_test=True,
        )
        old_run.context.completed_nodes.add("node-1")
        old_run.context.variables = {"input": {"key": "val"}}
        deps["run_repo"].save(old_run)
        wf.test_run_id = "old-run"
        deps["workflow_repo"].save(wf)

        uc = ResetTestRunUseCase(**deps)
        result = await uc.execute(ResetTestRunCommand(workflow_id="wf-1"))

        # Same run ID — never changes
        assert result.run_id == "old-run"

        # State cleared
        run = deps["run_repo"].get_by_id("old-run")
        assert run.status == RunStatus.CREATED
        assert len(run.context.completed_nodes) == 0
        assert run.context.variables == {}

    @pytest.mark.asyncio
    async def test_reset_deletes_prior_run_files(self, deps):
        # SA-1905 E retention: the prior run's file-backed outputs (streamed
        # CSV main + artifacts, nested in variables) must be deleted from the
        # file service before variables are wiped — else every re-run leaks.
        from app.layer1_domain.entities.data_reference import (
            add_artifact, build_file_ref,
        )
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)
        main = build_file_ref(file_id="f_main", columns=["a"])
        add_artifact(main, file_id="f_child", name="orders", artifact_id="orders")
        old_run = WorkflowRun(
            id="old-run", workflow_id="wf-1",
            status=RunStatus.RUNNING, is_test=True,
        )
        old_run.context.variables = {
            "gen": {"result": build_file_ref(file_id="f_gen", columns=["i"])},
            "out": {"output": {"du_lieu": main}},
        }
        deps["run_repo"].save(old_run)
        wf.test_run_id = "old-run"
        deps["workflow_repo"].save(wf)

        deleted: list[str] = []

        class _FC:
            async def delete_file(self, fid):
                deleted.append(fid)
                return {}

        uc = ResetTestRunUseCase(file_service_client=_FC(), **deps)
        await uc.execute(ResetTestRunCommand(workflow_id="wf-1"))

        assert set(deleted) == {"f_gen", "f_main", "f_child"}
        run = deps["run_repo"].get_by_id("old-run")
        assert run.context.variables == {}

    @pytest.mark.asyncio
    async def test_reset_survives_file_delete_failure(self, deps):
        # Best-effort GC: a file-service error must NOT fail the reset.
        from app.layer1_domain.entities.data_reference import build_file_ref
        wf = _make_workflow()
        deps["workflow_repo"].save(wf)
        old_run = WorkflowRun(
            id="old-run", workflow_id="wf-1",
            status=RunStatus.RUNNING, is_test=True,
        )
        old_run.context.variables = {"gen": build_file_ref(file_id="f_x", columns=["i"])}
        deps["run_repo"].save(old_run)
        wf.test_run_id = "old-run"
        deps["workflow_repo"].save(wf)

        class _Boom:
            async def delete_file(self, fid):
                raise RuntimeError("file service down")

        uc = ResetTestRunUseCase(file_service_client=_Boom(), **deps)
        result = await uc.execute(ResetTestRunCommand(workflow_id="wf-1"))
        assert result.error == ""   # reset still succeeded despite the error
        run = deps["run_repo"].get_by_id("old-run")
        assert run.context.variables == {}


# ── Credential seeding (regression: single-node test 401) ────────


class _FakeVaultNode:
    def __init__(self):
        self.calls = []

    async def resolve_by_id(self, tenant_id, credential_id, user_id=None):
        self.calls.append((tenant_id, credential_id, user_id))
        return {"type": "bearer", "config": {"token": "tok"}, "expires_at": "z"}


class _FakeCipherNode:
    def encrypt(self, data):
        return {"__enc__": data}


@pytest.mark.asyncio
async def test_test_node_no_longer_seeds_credentials_onto_the_run():
    """A single-node test must NOT seal ``__credentials`` onto the run.

    This test used to assert the opposite, and the reason it changed is the
    whole of §13.6: that map was the last thing keeping ``CONTEXT`` alive. It is
    not event-sourced (LAI-19 keeps the sealed map out of any replayable store)
    and no new table may hold it, so ``DROP CONTEXT`` would have destroyed it —
    silently, on every reload, for every credential-using worker.

    The node still authenticates: it declares a ``credential_ref``, and
    ``RunCredentialProvider`` resolves that from the vault when the node
    dispatches (SA-2287 / B3), which is why the eager seal was redundant here.
    This path was also the worse of the two writers — it sealed the map WITHOUT
    encryption whenever ``GATEWAY_REGISTRY_KEK_V1`` was unset.

    What is asserted now is the absence, so nothing re-introduces the write
    without this failing."""
    from app.layer4_frameworks.persistence.memory.memory_workflow_snapshot_repo import (
        MemoryWorkflowSnapshotRepository,
    )
    wf_repo, run_repo, task_repo = MemRepo(), MemRepo(), MemRepo()
    wf = Workflow(
        id="wf-1", name="WF",
        nodes=[WorkflowNode(id="task-1", name="Task1", node_type=NodeType.TASK,
                            worker_type="w1", config={"credential_ref": "c1"})],
        edges=[],
    )
    wf_repo.save(wf)
    vault = _FakeVaultNode()
    uc = TestNodeUseCase(
        logger=DummyLogger(), workflow_repo=wf_repo,
        workflow_snapshot_repo=MemoryWorkflowSnapshotRepository(),
        run_repo=run_repo, task_repo=task_repo,
        handler_registry=_make_handler_registry(), orchestration_engine=_make_engine(),
        stage_recorder=DummyStageRecorder(),
        credential_cipher=_FakeCipherNode(), vault_credential_client=vault,
    )

    result = await uc.execute(TestNodeCommand(workflow_id="wf-1", node_id="task-1"))
    assert result.error == ""
    run = run_repo.get_by_id(result.run_id)
    assert "__credentials" not in run.context.variables
    # And nothing is resolved at start either: the eager whole-workflow walk is
    # gone, so the vault is not called until the node actually dispatches.
    assert vault.calls == []
