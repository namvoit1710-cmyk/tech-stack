"""Unit tests for feature use cases."""

import pytest
import dataclasses
from unittest.mock import MagicMock, AsyncMock, patch

from app.layer2_application.features.create_workflow.use_cases.create_workflow_use_case import (
    CreateWorkflowCommand, CreateWorkflowUseCase,
)
from app.layer2_application.features.get_workflow.use_cases.get_workflow_use_case import (
    GetWorkflowCommand, GetWorkflowUseCase,
)
from app.layer2_application.features.list_workflows.use_cases.list_workflows_use_case import (
    ListWorkflowsCommand, ListWorkflowsUseCase,
)
from app.layer2_application.features.update_workflow.use_cases.update_workflow_use_case import (
    UpdateWorkflowCommand, UpdateWorkflowUseCase,
)
from app.layer2_application.features.delete_workflow.use_cases.delete_workflow_use_case import (
    DeleteWorkflowCommand, DeleteWorkflowUseCase,
)
from app.layer2_application.features.get_run_status.use_cases.get_run_status_use_case import (
    GetRunStatusCommand, GetRunStatusUseCase,
)
from app.layer2_application.features.cancel_run.use_cases.cancel_run_use_case import (
    CancelRunCommand, CancelRunUseCase,
)
from app.layer2_application.features.store_output.use_cases.store_output_use_case import (
    StoreOutputCommand, StoreOutputUseCase,
)
from app.layer2_application.features.get_output.use_cases.get_output_use_case import (
    GetOutputCommand, GetOutputUseCase,
)
from app.layer2_application.features.list_node_palette.use_cases.list_node_palette_use_case import (
    ListNodePaletteCommand, ListNodePaletteUseCase,
)
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand, StartRunUseCase,
)
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskCommand, CompleteTaskUseCase,
)
from app.layer2_application.features.fail_task.use_cases.fail_task_use_case import (
    FailTaskCommand, FailTaskUseCase,
)
from app.layer2_application.features.pause_run.use_cases.pause_run_use_case import (
    PauseRunCommand, PauseRunUseCase,
)
from app.layer2_application.features.resume_run.use_cases.resume_run_use_case import (
    ResumeRunCommand, ResumeRunUseCase,
)
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects.workflow_status import WorkflowStatus
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.entities.task import Task
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.node_handlers.base import NodeHandlerRegistry, NodeHandlerResult
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.node_handlers.task_handler import TaskNodeHandler
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
from app.layer2_application.resolve.variable_resolver import VariableResolver, TraversalResolverStrategy
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import MemoryStageHistoryRepository


# ────────────── Workflow Management ──────────────


class TestCreateWorkflow:
    @pytest.mark.asyncio
    async def test_create_workflow(self, logger, workflow_repo):
        uc = CreateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        command = CreateWorkflowCommand(name="My Workflow", description="desc")
        result = await uc.execute(command)
        assert result.name == "My Workflow"
        assert result.description == "desc"
        assert result.id != ""
        assert result.status == "DRAFT"

    @pytest.mark.asyncio
    async def test_create_workflow_with_nodes(self, logger, workflow_repo):
        uc = CreateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        command = CreateWorkflowCommand(
            name="Node WF",
            nodes=[
                {"id": "n1", "name": "Start", "node_type": "TRIGGER"},
                {"id": "n2", "name": "End", "node_type": "TASK"},
            ],
            edges=[{"source_node_id": "n1", "target_node_id": "n2"}],
        )
        result = await uc.execute(command)
        assert len(result.nodes) == 2
        assert result.nodes[0]["name"] == "Start"


class TestGetWorkflow:
    @pytest.mark.asyncio
    async def test_get_existing_workflow(self, logger, workflow_repo, sample_workflow):
        workflow_repo.save(sample_workflow)
        uc = GetWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        result = await uc.execute(GetWorkflowCommand(workflow_id="wf-1"))
        assert result.id == "wf-1"
        assert result.name == "Test Workflow"
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_get_missing_workflow(self, logger, workflow_repo):
        uc = GetWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        result = await uc.execute(GetWorkflowCommand(workflow_id="missing"))
        assert result.error == "Workflow not found"


class TestListWorkflows:
    @pytest.mark.asyncio
    async def test_list_empty(self, logger, workflow_repo):
        uc = ListWorkflowsUseCase(logger=logger, workflow_repo=workflow_repo)
        result = await uc.execute(ListWorkflowsCommand())
        assert result.workflows == []

    @pytest.mark.asyncio
    async def test_list_with_workflows(self, logger, workflow_repo, sample_workflow):
        workflow_repo.save(sample_workflow)
        uc = ListWorkflowsUseCase(logger=logger, workflow_repo=workflow_repo)
        result = await uc.execute(ListWorkflowsCommand())
        assert len(result.workflows) == 1
        assert result.workflows[0]["id"] == "wf-1"


class TestDeleteWorkflow:
    @pytest.mark.asyncio
    async def test_delete_workflow(self, logger, workflow_repo, sample_workflow):
        workflow_repo.save(sample_workflow)
        uc = DeleteWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        result = await uc.execute(DeleteWorkflowCommand(workflow_id="wf-1"))
        assert result.deleted is True
        assert workflow_repo.get_by_id("wf-1") is None


# ────────────── Run Status ──────────────


class TestGetRunStatus:
    @pytest.mark.asyncio
    async def test_get_run_status(self, logger, run_repo, sample_run):
        from tests.helpers.view_store_doubles import LiveViewStoreDouble as LiveRunContextViewStore
        run_repo.save(sample_run)
        uc = GetRunStatusUseCase(
            logger=logger,
            run_context_view_store=LiveRunContextViewStore(run_repo),
        )
        result = await uc.execute(GetRunStatusCommand(run_id="run-1"))
        assert result.id == "run-1"
        assert result.status == "RUNNING"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_get_missing_run(self, logger, run_repo):
        from tests.helpers.view_store_doubles import LiveViewStoreDouble as LiveRunContextViewStore
        uc = GetRunStatusUseCase(
            logger=logger,
            run_context_view_store=LiveRunContextViewStore(run_repo),
        )
        result = await uc.execute(GetRunStatusCommand(run_id="missing"))
        assert result.error == "Run not found"


class TestCancelRun:
    @pytest.mark.asyncio
    async def test_cancel_running_run(self, logger, run_repo, task_repo, stage_recorder, orchestration_engine, sample_run):
        run_repo.save(sample_run)
        uc = CancelRunUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
        )
        result = await uc.execute(CancelRunCommand(run_id="run-1"))
        assert result.cancelled is True
        # Phase 3b.2c: status lives on the projector — the aggregate
        # no longer tracks it. Assert via the emitted event instead.
        assert len(orchestration_engine.emitted.get("emit_run_cancelled", [])) == 1

    @pytest.mark.asyncio
    async def test_cancel_missing_run(self, logger, run_repo, task_repo, stage_recorder, orchestration_engine):
        uc = CancelRunUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
        )
        result = await uc.execute(CancelRunCommand(run_id="missing"))
        assert result.cancelled is False
        assert result.error == "Run not found"

    @pytest.mark.asyncio
    async def test_cancel_completed_run(self, logger, run_repo, task_repo, stage_recorder, orchestration_engine):
        run = WorkflowRun(id="run-done", workflow_id="wf-1", status=RunStatus.COMPLETED)
        run_repo.save(run)
        uc = CancelRunUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
        )
        result = await uc.execute(CancelRunCommand(run_id="run-done"))
        assert result.cancelled is False
        assert result.error_code == "INVALID_RUN_STATUS"

    @pytest.mark.asyncio
    async def test_cancel_created_run(self, logger, run_repo, task_repo, stage_recorder, orchestration_engine):
        # CHANGED in SA-2257. This used to assert the opposite, on the
        # rationale that "CREATED is not yet active and stays
        # non-cancellable". That left CREATED as the only status with no path
        # to a terminal state at all: measured on tenant-1, 117 runs stuck in
        # it, the oldest for 31 days, each holding its per-run lock and caches
        # forever because those are released on terminal transitions only.
        #
        # The refusal did protect something real — ``_orchestrate_start_inner``
        # had no status guard, so a StartRunAction arriving after a cancel
        # would resurrect the run. That hole is now closed at its source (the
        # engine refuses to start an already-terminal run, reading the
        # AUTHORITATIVE status because a cancel does not write the aggregate's
        # STATUS column). Guarding the start is the correct fix; refusing the
        # cancel only hid the race behind an unreachable state.
        run = WorkflowRun(id="run-new", workflow_id="wf-1", status=RunStatus.CREATED)
        run_repo.save(run)
        uc = CancelRunUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
        )
        result = await uc.execute(CancelRunCommand(run_id="run-new"))
        assert result.cancelled is True
        assert not result.error_code

    @pytest.mark.asyncio
    async def test_cancel_pending_run(self, logger, run_repo, task_repo, stage_recorder, orchestration_engine):
        # A run parked at an interactive node (awaiting user input) is cancellable.
        run = WorkflowRun(id="run-pend", workflow_id="wf-1", status=RunStatus.PENDING)
        run_repo.save(run)
        t1 = Task(id="t1", run_id="run-pend", node_id="n1", status=TaskStatus.PENDING)
        task_repo.save(t1)
        uc = CancelRunUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
        )
        result = await uc.execute(CancelRunCommand(run_id="run-pend"))
        assert result.cancelled is True
        assert len(orchestration_engine.emitted.get("emit_run_cancelled", [])) == 1
        updated_t1 = task_repo.get_by_id("t1")
        assert updated_t1.status == TaskStatus.CANCELLED
        assert updated_t1.output_metadata["reason"] == "run_cancelled"

    @pytest.mark.asyncio
    async def test_cancel_suspended_run(self, logger, run_repo, task_repo, stage_recorder, orchestration_engine):
        # A manual-mode run that hasn't started (no tasks) is cancellable.
        run = WorkflowRun(id="run-susp", workflow_id="wf-1", status=RunStatus.SUSPENDED)
        run_repo.save(run)
        uc = CancelRunUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
        )
        result = await uc.execute(CancelRunCommand(run_id="run-susp"))
        assert result.cancelled is True
        assert len(orchestration_engine.emitted.get("emit_run_cancelled", [])) == 1

    @pytest.mark.asyncio
    async def test_cancel_run_with_active_tasks(self, logger, run_repo, task_repo, stage_recorder, orchestration_engine):
        run = WorkflowRun(id="run-act", workflow_id="wf-1", status=RunStatus.RUNNING)
        run_repo.save(run)
        # Add tasks in various states
        t1 = Task(id="t1", run_id="run-act", node_id="n1", status=TaskStatus.RUNNING)
        t2 = Task(id="t2", run_id="run-act", node_id="n2", status=TaskStatus.COMPLETED)
        t3 = Task(id="t3", run_id="run-act", node_id="n3", status=TaskStatus.READY)
        task_repo.save(t1)
        task_repo.save(t2)
        task_repo.save(t3)
        uc = CancelRunUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
        )
        result = await uc.execute(CancelRunCommand(run_id="run-act"))
        assert result.cancelled is True
        # RUNNING and READY tasks should be marked as CANCELLED
        updated_t1 = task_repo.get_by_id("t1")
        assert updated_t1.status == TaskStatus.CANCELLED
        assert updated_t1.output_metadata["reason"] == "run_cancelled"
        # COMPLETED task should remain unchanged
        updated_t2 = task_repo.get_by_id("t2")
        assert updated_t2.output_metadata is None  # was already completed


# ────────────── Output ──────────────


class TestStoreOutput:
    @pytest.mark.asyncio
    async def test_store_output(self, logger):
        uc = StoreOutputUseCase(logger=logger)
        result = await uc.execute(StoreOutputCommand(run_id="run-1", data={"key": "val"}))
        assert result.stored is True
        assert result.run_id == "run-1"
        assert "run-1" in result.reference


class TestGetOutput:
    @pytest.mark.asyncio
    async def test_get_output_missing_run(self, logger, run_repo):
        uc = GetOutputUseCase(logger=logger, run_repo=run_repo)
        result = await uc.execute(GetOutputCommand(run_id="missing"))
        assert result.error == "Run not found"


# ────────────── Node Palette ──────────────


class TestListNodePalette:
    @pytest.mark.asyncio
    async def test_list_builtin_nodes(self, logger):
        uc = ListNodePaletteUseCase(logger=logger)
        result = await uc.execute(ListNodePaletteCommand())
        assert "control_flow" in result.categories
        assert result.total >= 8  # 8 built-in node types

        required_field_keys = {"key", "outputType", "fieldConfig"}
        # PARALLEL legitimately has an empty config form since the dead
        # max_concurrency/fail_strategy fields were removed (NodeSpec
        # redesign — docs/design/node-spec-fe-changes.md).
        may_have_empty_input = {"Parallel"}
        for node in result.categories["control_flow"]:
            assert "config_hints" not in node, f"{node['name']} should not have config_hints"
            assert isinstance(node.get("input_schema"), list), f"{node['name']} missing input_schema"
            assert isinstance(node.get("output_schema"), list), f"{node['name']} missing output_schema"
            if node["name"] not in may_have_empty_input:
                assert len(node["input_schema"]) > 0, f"{node['name']} has empty input_schema"
            assert len(node["output_schema"]) > 0, f"{node['name']} has empty output_schema"
            for field_def in node["input_schema"] + node["output_schema"]:
                assert required_field_keys.issubset(field_def.keys()), (
                    f"{node['name']} schema field {field_def.get('key')} missing required keys"
                )

    @pytest.mark.asyncio
    async def test_list_with_workers(self, logger):
        from tests.conftest import DummyWorkerRegistryClient
        sample_input_schema = [
            {
                "key": "url",
                "outputType": "string",
                "fieldConfig": {
                    "label": "URL",
                    "description": "Target URL",
                    "fieldControl": "Input",
                    "fieldWrapper": "FormItem",
                },
                "rules": {"isRequired": True},
            },
        ]
        sample_output_schema = [
            {
                "key": "response_body",
                "outputType": "string",
                "fieldConfig": {"label": "Response Body"},
            },
        ]
        client = DummyWorkerRegistryClient(workers=[
            {
                "id": "w1",
                "name": "test-worker",
                "health_status": "healthy",
                "worker_type": "http-request",
                "version": "1.0.0",
                "capabilities": [],
                "input_schema": sample_input_schema,
                "output_schema": sample_output_schema,
            },
        ])
        uc = ListNodePaletteUseCase(logger=logger, worker_registry_client=client)
        result = await uc.execute(ListNodePaletteCommand())
        assert len(result.categories["worker"]) == 1
        worker_node = result.categories["worker"][0]
        assert worker_node["name"] == "test-worker"
        assert worker_node["worker_type"] == "http-request"
        assert worker_node["version"] == "1.0.0"
        assert worker_node["input_schema"] == sample_input_schema
        assert worker_node["output_schema"] == sample_output_schema
        assert worker_node["input_schema"][0]["key"] == "url"
        assert worker_node["input_schema"][0]["fieldConfig"]["fieldControl"] == "Input"

    @pytest.mark.asyncio
    async def test_list_with_workers_no_schema(self, logger):
        """Workers registered with a stable worker_type but no schemas should
        surface with empty lists for input/output_schema. Workers without
        worker_type are dropped (see Phase 0 Task 0.1)."""
        from tests.conftest import DummyWorkerRegistryClient
        client = DummyWorkerRegistryClient(workers=[
            {
                "id": "w2",
                "name": "bare-worker",
                "worker_type": "bare-worker",
                "health_status": "healthy",
                "capabilities": [],
            },
        ])
        uc = ListNodePaletteUseCase(logger=logger, worker_registry_client=client)
        result = await uc.execute(ListNodePaletteCommand())
        worker_node = result.categories["worker"][0]
        assert worker_node["input_schema"] == []
        assert worker_node["output_schema"] == []


# ────────────── Worker Registry Client ──────────────


class TestDummyWorkerRegistryClient:
    def test_get_worker_found(self):
        from tests.conftest import DummyWorkerRegistryClient
        client = DummyWorkerRegistryClient(workers=[
            {"id": "w1", "name": "worker-1", "health_status": "healthy"},
        ])
        result = client.get_worker("w1")
        assert result is not None
        assert result["id"] == "w1"

    def test_get_worker_not_found(self):
        from tests.conftest import DummyWorkerRegistryClient
        client = DummyWorkerRegistryClient(workers=[])
        assert client.get_worker("missing") is None

    def test_get_worker_schema(self):
        from tests.conftest import DummyWorkerRegistryClient
        client = DummyWorkerRegistryClient(workers=[
            {"id": "w1", "name": "worker-1", "worker_type": "llm", "version": "1.0",
             "input_schema": {"type": "object"}, "output_schema": {"type": "object"}},
        ])
        schema = client.get_worker_schema("w1")
        assert schema is not None
        assert schema["worker_id"] == "w1"
        assert schema["worker_type"] == "llm"

    def test_get_worker_schema_not_found(self):
        from tests.conftest import DummyWorkerRegistryClient
        client = DummyWorkerRegistryClient(workers=[])
        assert client.get_worker_schema("missing") is None

    def test_discover_workers(self):
        from tests.conftest import DummyWorkerRegistryClient
        client = DummyWorkerRegistryClient(workers=[
            {"id": "w1", "name": "worker-1", "capabilities": [{"domain": "nlp", "action": "summarize"}]},
            {"id": "w2", "name": "worker-2", "capabilities": [{"domain": "vision", "action": "classify"}]},
        ])
        results = client.discover_workers("nlp")
        assert len(results) == 1
        assert results[0]["id"] == "w1"

    def test_discover_workers_no_match(self):
        from tests.conftest import DummyWorkerRegistryClient
        client = DummyWorkerRegistryClient(workers=[
            {"id": "w1", "name": "worker-1", "capabilities": [{"domain": "nlp", "action": "summarize"}]},
        ])
        assert client.discover_workers("audio") == []


# ────────────── Auto-discovery ──────────────


class TestAutoDiscovery:
    def test_scan_features(self):
        from app.layer2_application.features import load_features, FEATURE_REGISTRY
        FEATURE_REGISTRY.clear()
        load_features()
        assert len(FEATURE_REGISTRY) >= 17
        expected = {
            "start_run", "complete_task", "fail_task", "cancel_run",
            "pause_run", "resume_run", "get_run_status",
            "create_workflow", "get_workflow", "list_workflows",
            "update_workflow", "delete_workflow",
            "store_output", "get_output", "list_node_palette",
            "dry_run_workflow",
        }
        assert expected.issubset(set(FEATURE_REGISTRY.keys()))


# ────────────── Smart Injection ──────────────


class TestSmartInjection:
    def test_bootstrap_container(self):
        from bootstrap import build_app_container
        container = build_app_container()
        # 17 features + _infra
        assert len(container) >= 18
        # Spot check a few use cases are correctly instantiated
        assert hasattr(container["create_workflow"], "execute")
        assert hasattr(container["start_run"], "execute")
        assert hasattr(container["list_node_palette"], "execute")
        assert container["_infra"] is not None


# ────────────── Helper: Real OrchestrationEngine builder ──────────────


def _build_real_engine(task_repo, run_repo, event_publisher=None, stage_history_repo=None, workflow_repo=None, workflow_snapshot_repo=None):
    """Build a real OrchestrationEngine with trigger+task handlers for integration-like tests.

    Also attaches a ``MemoryOrchestrationQueue`` so the use cases that call
    ``await self._queue.enqueue(...)`` succeed. Tests that want the queued
    actions to actually run should use ``attach_queue`` from
    ``tests.helpers.queue_runner`` instead (which exposes a ``QueueRunner``).

    If ``event_publisher`` is not provided a ``CapturingEventPublisher`` is
    used so tests can verify Phase 3b.1 write-set emissions without touching
    ``run.context.variables`` (which the engine no longer mutates).
    """
    from tests.helpers.queue_runner import attach_queue
    from tests.helpers.event_capture import CapturingEventPublisher
    from app.layer4_frameworks.persistence.memory.memory_workflow_snapshot_repo import (
        MemoryWorkflowSnapshotRepository,
    )

    resolver = VariableResolver([TraversalResolverStrategy()])
    registry = NodeHandlerRegistry()
    registry.register(NodeType.TRIGGER, TriggerNodeHandler())
    registry.register(NodeType.TASK, TaskNodeHandler(variable_resolver=resolver))

    sh_repo = stage_history_repo or MemoryStageHistoryRepository()
    stage_rec = StageRecorder(repository=sh_repo)
    successor_proc = SuccessorProcessor(
        handler_registry=registry,
        task_factory=TaskFactory(),
    )
    publisher = event_publisher if event_publisher is not None else CapturingEventPublisher()
    # Use the shared test-scoped snapshot repo so the run-save shim in
    # conftest writes to the same repo that the engine reads from.
    from tests.conftest import _get_shared_snapshot_repo
    snap_repo = workflow_snapshot_repo if workflow_snapshot_repo is not None else _get_shared_snapshot_repo()
    engine = OrchestrationEngine(
        handler_registry=registry,
        task_factory=TaskFactory(),
        successor_processor=successor_proc,
        stage_recorder=stage_rec,
        task_repo=task_repo,
        run_repo=run_repo,
        event_publisher=publisher,
        workflow_repo=workflow_repo,
        workflow_snapshot_repo=snap_repo,
    )
    queue, runner = attach_queue(engine)
    engine._test_runner = runner  # tests call ``await engine._test_runner.run_until_idle()`` if they need side effects
    engine._test_publisher = publisher  # exposed for projected-variables assertions
    engine._test_snapshot_repo = snap_repo  # exposed so tests can capture snapshots for hand-built runs
    return engine


# ────────────── StartRun ──────────────


class TestStartRunUseCase:
    @pytest.mark.asyncio
    async def test_start_run_success(self, logger, workflow_repo, run_repo, task_repo):
        wf = Workflow(
            id="wf-1",
            workflow_id="wf-1",
            status=WorkflowStatus.PUBLISHED,
            name="Test",
            nodes=[
                WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
                WorkflowNode(id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
            ],
            edges=[WorkflowEdge(source_node_id="t", target_node_id="task1")],
        )
        workflow_repo.save(wf)

        engine = _build_real_engine(task_repo, run_repo)
        sh_repo = MemoryStageHistoryRepository()
        stage_rec = StageRecorder(repository=sh_repo)

        uc = StartRunUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_rec,
        )
        result = await uc.execute(StartRunCommand(
            workflow_id="wf-1",
            input_data={"key": "val"},
            tenant_id="t1",
            correlation_id="corr-1",
        ))

        assert result.workflow_id == "wf-1"
        assert result.status in ("RUNNING", "COMPLETED")
        assert result.error is None

    @pytest.mark.asyncio
    async def test_start_run_missing_workflow(self, logger, workflow_repo, run_repo, task_repo):
        engine = _build_real_engine(task_repo, run_repo)
        sh_repo = MemoryStageHistoryRepository()
        stage_rec = StageRecorder(repository=sh_repo)

        uc = StartRunUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_rec,
        )
        result = await uc.execute(StartRunCommand(workflow_id="missing"))

        assert result.error is not None
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_start_run_with_task_entry_node(self, logger, workflow_repo, run_repo, task_repo):
        """Test start run when entry node is a TASK (non-builtin), covering lines 121-123."""
        wf = Workflow(
            id="wf-task-entry",
            workflow_id="wf-task-entry",
            status=WorkflowStatus.PUBLISHED,
            name="Test",
            nodes=[
                WorkflowNode(id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
            ],
            edges=[],
        )
        workflow_repo.save(wf)

        engine = _build_real_engine(task_repo, run_repo)
        sh_repo = MemoryStageHistoryRepository()
        stage_rec = StageRecorder(repository=sh_repo)

        uc = StartRunUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_rec,
        )
        result = await uc.execute(StartRunCommand(
            workflow_id="wf-task-entry",
            input_data={"key": "val"},
        ))

        assert result.status in ("RUNNING", "COMPLETED")

    @pytest.mark.asyncio
    async def test_start_run_emits_run_started_in_queue_path(
        self, logger, workflow_repo, run_repo, task_repo,
    ):
        """Regression: ``run.started`` must reach the publisher even when
        StartRunUseCase takes the orchestration-queue path (production
        configuration). Previously, the publish_event was buffered into a
        ContextVar-scoped EventCollector that the queue-worker's separate
        async context never drained — so the event was silently lost.
        """
        from app.layer2_application.orchestration_queue.memory_queue import (
            MemoryOrchestrationQueue,
        )

        wf = Workflow(
            id="wf-1",
            workflow_id="wf-1",
            status=WorkflowStatus.PUBLISHED,
            name="Test",
            nodes=[
                WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
            ],
            edges=[],
        )
        workflow_repo.save(wf)

        # Capture every (topic, event) the engine publishes.
        captured: list[tuple[str, object]] = []

        class _Capturing:
            def publish(self, topic, event):
                captured.append((topic, event))

        engine = _build_real_engine(task_repo, run_repo, event_publisher=_Capturing())
        sh_repo = MemoryStageHistoryRepository()
        stage_rec = StageRecorder(repository=sh_repo)
        queue = MemoryOrchestrationQueue()

        uc = StartRunUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_rec,
            orchestration_queue=queue,  # ← takes the queue branch
        )
        result = await uc.execute(StartRunCommand(workflow_id="wf-1"))
        assert result.error is None

        topics = [t for t, _ in captured]
        assert "run.status" in topics, (
            f"run.status must reach the publisher in the queue path, "
            f"got topics={topics}. The publish_event in StartRunUseCase "
            f"buffers via EventCollector (context-local) — without an "
            f"explicit drain_events() before queue.enqueue, the event is "
            f"stranded when the queue worker runs in a different context."
        )
        run_status_events = [e for t, e in captured if t == "run.status"]
        assert any(getattr(e, "status", "") == "started" for e in run_status_events), (
            f"a run.status event with status='started' must be published; "
            f"got {[getattr(e, 'status', None) for e in run_status_events]}"
        )


# ────────────── CompleteTask ──────────────


class TestCompleteTaskUseCase:
    def _setup(self, logger, workflow_repo, run_repo, task_repo):
        wf = Workflow(
            id="wf-1",
            name="Test",
            nodes=[
                WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
                WorkflowNode(id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
            ],
            edges=[WorkflowEdge(source_node_id="t", target_node_id="task1")],
        )
        workflow_repo.save(wf)

        topology = GraphTopology(
            adjacency={"t": ["task1"], "task1": []},
            predecessors={"t": [], "task1": ["t"]},
            edge_map={"t": wf.edges, "task1": []},
            entry_nodes=["t"],
            leaf_nodes=["task1"],
        )

        run = WorkflowRun(
            id="run-1",
            workflow_id="wf-1",
            status=RunStatus.RUNNING,
            context=RunContext(
                topology=topology,
                completed_nodes={"t"},
                variables={"t": {"output": {}}},
            ),
        )
        run_repo.save(run)

        task = Task(
            id="task-1",
            run_id="run-1",
            node_id="task1",
            status=TaskStatus.RUNNING,
            worker_type="w1",
        )
        task_repo.save(task)

        engine = _build_real_engine(task_repo, run_repo)
        sh_repo = MemoryStageHistoryRepository()
        stage_rec = StageRecorder(repository=sh_repo)

        return engine, stage_rec

    @pytest.mark.asyncio
    async def test_complete_task_success(self, logger, workflow_repo, run_repo, task_repo):
        engine, stage_rec = self._setup(logger, workflow_repo, run_repo, task_repo)

        uc = CompleteTaskUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_rec,
        )
        result = await uc.execute(CompleteTaskCommand(
            run_id="run-1",
            task_id="task-1",
            output_reference="s3://bucket/output",
            output_metadata={"key": "val"},
        ))

        assert result.completed is True
        assert result.status == "COMPLETED"
        task = task_repo.get_by_id("task-1")
        assert task.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_complete_task_missing_run(self, logger, workflow_repo, run_repo, task_repo):
        engine = _build_real_engine(task_repo, run_repo)
        sh_repo = MemoryStageHistoryRepository()
        stage_rec = StageRecorder(repository=sh_repo)

        uc = CompleteTaskUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_rec,
        )
        result = await uc.execute(CompleteTaskCommand(run_id="missing", task_id="t1"))

        assert result.error_code == "RUN_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_complete_task_missing_task(self, logger, workflow_repo, run_repo, task_repo):
        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
        run_repo.save(run)

        engine = _build_real_engine(task_repo, run_repo)
        sh_repo = MemoryStageHistoryRepository()
        stage_rec = StageRecorder(repository=sh_repo)

        uc = CompleteTaskUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_rec,
        )
        result = await uc.execute(CompleteTaskCommand(run_id="run-1", task_id="missing"))

        assert result.error_code == "TASK_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_complete_task_idempotent(self, logger, workflow_repo, run_repo, task_repo):
        # Option 1 (re-drive on replay): completing an already-COMPLETED task
        # re-runs the body without raising — idempotency comes from the sinks
        # (deterministic NodeOutputProduced dedupe + completed_nodes frozenset
        # fold), not a terminal-status short-circuit (removed). Previously this
        # asserted ``result.idempotent is True`` from that guard.
        engine, stage_rec = self._setup(logger, workflow_repo, run_repo, task_repo)
        task = task_repo.get_by_id("task-1")
        task.status = TaskStatus.COMPLETED
        task_repo.save(task)

        uc = CompleteTaskUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_rec,
        )
        result = await uc.execute(CompleteTaskCommand(run_id="run-1", task_id="task-1"))

        assert result.completed is True  # re-drove, no raise
        assert task_repo.get_by_id("task-1").status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_complete_task_missing_workflow(self, logger, workflow_repo, run_repo, task_repo):
        from app.layer1_domain.exceptions import WorkflowSnapshotMissingError
        run = WorkflowRun(id="run-1", workflow_id="wf-missing", status=RunStatus.RUNNING)
        run_repo.save(run)
        task = Task(id="task-1", run_id="run-1", node_id="n1", status=TaskStatus.RUNNING)
        task_repo.save(task)

        engine = _build_real_engine(task_repo, run_repo)
        sh_repo = MemoryStageHistoryRepository()
        stage_rec = StageRecorder(repository=sh_repo)

        uc = CompleteTaskUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_rec,
        )
        # Phase B+C: ``load_workflow_for_run`` now hard-fails with
        # ``WorkflowSnapshotMissingError`` when the run has no resolvable
        # snapshot, instead of returning ``None`` for the use case to
        # translate into ``WORKFLOW_NOT_FOUND``. Asserting the new
        # contract directly.
        with pytest.raises(WorkflowSnapshotMissingError):
            await uc.execute(CompleteTaskCommand(run_id="run-1", task_id="task-1"))

    @pytest.mark.asyncio
    async def test_complete_task_with_output_binding(self, logger, workflow_repo, run_repo, task_repo):
        resolver = VariableResolver([TraversalResolverStrategy()])
        wf = Workflow(
            id="wf-1",
            name="Test",
            nodes=[
                WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
                WorkflowNode(
                    id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1",
                    output_binding={"total": "{{$_output.metadata.amount}}"},
                ),
            ],
            edges=[WorkflowEdge(source_node_id="t", target_node_id="task1")],
        )
        workflow_repo.save(wf)
        topology = GraphTopology(
            adjacency={"t": ["task1"], "task1": []},
            predecessors={"t": [], "task1": ["t"]},
            edge_map={"t": wf.edges, "task1": []},
            entry_nodes=["t"],
            leaf_nodes=["task1"],
        )
        run = WorkflowRun(
            id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING,
            context=RunContext(topology=topology, completed_nodes={"t"}, variables={"t": {"output": {}}}),
        )
        run_repo.save(run)
        task = Task(id="task-1", run_id="run-1", node_id="task1", status=TaskStatus.RUNNING, worker_type="w1")
        task_repo.save(task)

        engine = _build_real_engine(task_repo, run_repo)
        stage_rec = StageRecorder(repository=MemoryStageHistoryRepository())
        uc = CompleteTaskUseCase(
            logger=logger, workflow_repo=workflow_repo, run_repo=run_repo,
            task_repo=task_repo, orchestration_engine=engine,
            stage_recorder=stage_rec, variable_resolver=resolver,
        )
        result = await uc.execute(CompleteTaskCommand(
            run_id="run-1", task_id="task-1",
            output_reference="s3://bucket/out",
            output_metadata={"amount": 99},
        ))
        assert result.completed is True
        # Phase 3b.1: the task output travels via NodeOutputProduced —
        # assert the projected write-set instead of the aggregate.
        projected = engine._test_publisher.projected_variables()
        assert projected["task1"] == {"total": 99}

    @pytest.mark.asyncio
    async def test_complete_task_empty_output_binding_uses_raw(self, logger, workflow_repo, run_repo, task_repo):
        resolver = VariableResolver([TraversalResolverStrategy()])
        wf = Workflow(
            id="wf-1", name="Test",
            nodes=[
                WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
                WorkflowNode(id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1", output_binding={}),
            ],
            edges=[WorkflowEdge(source_node_id="t", target_node_id="task1")],
        )
        workflow_repo.save(wf)
        topology = GraphTopology(
            adjacency={"t": ["task1"], "task1": []},
            predecessors={"t": [], "task1": ["t"]},
            edge_map={"t": wf.edges, "task1": []},
            entry_nodes=["t"], leaf_nodes=["task1"],
        )
        run = WorkflowRun(
            id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING,
            context=RunContext(topology=topology, completed_nodes={"t"}, variables={"t": {"output": {}}}),
        )
        run_repo.save(run)
        task = Task(id="task-1", run_id="run-1", node_id="task1", status=TaskStatus.RUNNING, worker_type="w1")
        task_repo.save(task)

        engine = _build_real_engine(task_repo, run_repo)
        stage_rec = StageRecorder(repository=MemoryStageHistoryRepository())
        uc = CompleteTaskUseCase(
            logger=logger, workflow_repo=workflow_repo, run_repo=run_repo,
            task_repo=task_repo, orchestration_engine=engine,
            stage_recorder=stage_rec, variable_resolver=resolver,
        )
        result = await uc.execute(CompleteTaskCommand(
            run_id="run-1", task_id="task-1",
            output_reference="s3://bucket/out", output_metadata={"k": "v"},
        ))
        assert result.completed is True
        projected = engine._test_publisher.projected_variables()
        assert projected["task1"]["metadata"] == {"k": "v"}

    @pytest.mark.asyncio
    async def test_complete_task_no_resolver_uses_raw(self, logger, workflow_repo, run_repo, task_repo):
        wf = Workflow(
            id="wf-1", name="Test",
            nodes=[
                WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
                WorkflowNode(
                    id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1",
                    output_binding={"total": "{{$_output.metadata.amount}}"},
                ),
            ],
            edges=[WorkflowEdge(source_node_id="t", target_node_id="task1")],
        )
        workflow_repo.save(wf)
        topology = GraphTopology(
            adjacency={"t": ["task1"], "task1": []},
            predecessors={"t": [], "task1": ["t"]},
            edge_map={"t": wf.edges, "task1": []},
            entry_nodes=["t"], leaf_nodes=["task1"],
        )
        run = WorkflowRun(
            id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING,
            context=RunContext(topology=topology, completed_nodes={"t"}, variables={"t": {"output": {}}}),
        )
        run_repo.save(run)
        task = Task(id="task-1", run_id="run-1", node_id="task1", status=TaskStatus.RUNNING, worker_type="w1")
        task_repo.save(task)

        engine = _build_real_engine(task_repo, run_repo)
        stage_rec = StageRecorder(repository=MemoryStageHistoryRepository())
        uc = CompleteTaskUseCase(
            logger=logger, workflow_repo=workflow_repo, run_repo=run_repo,
            task_repo=task_repo, orchestration_engine=engine,
            stage_recorder=stage_rec, variable_resolver=None,
        )
        result = await uc.execute(CompleteTaskCommand(
            run_id="run-1", task_id="task-1",
            output_reference="s3://bucket/out", output_metadata={"amount": 99},
        ))
        assert result.completed is True
        # Raw output stored since no resolver — verified via emitted write-set.
        projected = engine._test_publisher.projected_variables()
        assert projected["task1"]["metadata"] == {"amount": 99}

    @pytest.mark.asyncio
    async def test_complete_task_output_binding_failure_fallback(self, logger, workflow_repo, run_repo, task_repo):
        resolver = VariableResolver([TraversalResolverStrategy()])
        wf = Workflow(
            id="wf-1", name="Test",
            nodes=[
                WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
                WorkflowNode(
                    id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1",
                    output_binding={"total": "{{$_output.metadata.missing_key}}"},
                ),
            ],
            edges=[WorkflowEdge(source_node_id="t", target_node_id="task1")],
        )
        workflow_repo.save(wf)
        topology = GraphTopology(
            adjacency={"t": ["task1"], "task1": []},
            predecessors={"t": [], "task1": ["t"]},
            edge_map={"t": wf.edges, "task1": []},
            entry_nodes=["t"], leaf_nodes=["task1"],
        )
        run = WorkflowRun(
            id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING,
            context=RunContext(topology=topology, completed_nodes={"t"}, variables={"t": {"output": {}}}),
        )
        run_repo.save(run)
        task = Task(id="task-1", run_id="run-1", node_id="task1", status=TaskStatus.RUNNING, worker_type="w1")
        task_repo.save(task)

        engine = _build_real_engine(task_repo, run_repo)
        stage_rec = StageRecorder(repository=MemoryStageHistoryRepository())
        uc = CompleteTaskUseCase(
            logger=logger, workflow_repo=workflow_repo, run_repo=run_repo,
            task_repo=task_repo, orchestration_engine=engine,
            stage_recorder=stage_rec, variable_resolver=resolver,
        )
        result = await uc.execute(CompleteTaskCommand(
            run_id="run-1", task_id="task-1",
            output_reference="s3://bucket/out", output_metadata={"amount": 99},
        ))
        assert result.completed is True
        # Fallback to raw output on resolution failure — verified on the event.
        projected = engine._test_publisher.projected_variables()
        assert projected["task1"]["metadata"] == {"amount": 99}


# ────────────── FailTask ──────────────


class TestFailTaskUseCase:
    # Tests in this class build a minimal placeholder workflow so the
    # snapshot loader can resolve a definition for the run.
    @staticmethod
    def _make_min_wf():
        return Workflow(
            id="wf-1", name="Test",
            nodes=[WorkflowNode(id="n1", name="Task1", node_type=NodeType.TASK, worker_type="w1")],
            edges=[],
        )

    @pytest.mark.asyncio
    async def test_fail_task_permanently(self, logger, workflow_repo, run_repo, task_repo, stage_recorder):
        workflow_repo.save(self._make_min_wf())
        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
        run_repo.save(run)
        task = Task(id="task-1", run_id="run-1", node_id="n1", status=TaskStatus.RUNNING)
        task_repo.save(task)

        engine = _build_real_engine(task_repo, run_repo, workflow_repo=workflow_repo)

        uc = FailTaskUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_recorder,
        )
        result = await uc.execute(FailTaskCommand(
            run_id="run-1",
            task_id="task-1",
            error={"message": "something went wrong", "retryable": False},
        ))
        await engine._test_runner.run_until_idle()

        assert result.failed is True
        assert "something went wrong" in result.error
        updated_task = task_repo.get_by_id("task-1")
        assert updated_task.status == TaskStatus.FAILED
        updated_run = run_repo.get_by_id("run-1")
        assert updated_run.status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_fail_task_ignores_retryable_flag(self, logger, workflow_repo, run_repo, task_repo, stage_recorder):
        """Retry-on-fail is disabled: even with ``retryable=True`` and
        remaining budget, the task is marked FAILED on the first failure.
        """
        workflow_repo.save(self._make_min_wf())
        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
        run_repo.save(run)
        task = Task(
            id="task-1", run_id="run-1", node_id="n1",
            status=TaskStatus.RUNNING, retry_count=0, max_retries=3,
        )
        task_repo.save(task)

        engine = _build_real_engine(task_repo, run_repo)

        uc = FailTaskUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_recorder,
        )
        result = await uc.execute(FailTaskCommand(
            run_id="run-1",
            task_id="task-1",
            error={"message": "timeout", "retryable": True},
        ))

        assert result.failed is True
        assert result.retrying is False
        updated_task = task_repo.get_by_id("task-1")
        assert updated_task.status == TaskStatus.FAILED
        assert updated_task.retry_count == 0

    @pytest.mark.asyncio
    async def test_fail_task_missing_run(self, logger, workflow_repo, run_repo, task_repo, stage_recorder):
        engine = _build_real_engine(task_repo, run_repo)

        uc = FailTaskUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_recorder,
        )
        result = await uc.execute(FailTaskCommand(run_id="missing", task_id="t1"))

        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_fail_task_missing_task(self, logger, workflow_repo, run_repo, task_repo, stage_recorder):
        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
        run_repo.save(run)

        engine = _build_real_engine(task_repo, run_repo)

        uc = FailTaskUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_recorder,
        )
        result = await uc.execute(FailTaskCommand(run_id="run-1", task_id="missing"))

        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_fail_task_retry_exhausted(self, logger, workflow_repo, run_repo, task_repo, stage_recorder):
        workflow_repo.save(self._make_min_wf())
        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
        run_repo.save(run)
        task = Task(
            id="task-1", run_id="run-1", node_id="n1",
            status=TaskStatus.RUNNING, retry_count=3, max_retries=3,
        )
        task_repo.save(task)

        engine = _build_real_engine(task_repo, run_repo)

        uc = FailTaskUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_recorder,
        )
        result = await uc.execute(FailTaskCommand(
            run_id="run-1",
            task_id="task-1",
            error={"message": "timeout", "retryable": True},
        ))

        assert result.failed is True
        assert result.retrying is False

    @pytest.mark.asyncio
    async def test_fail_task_string_error(self, logger, workflow_repo, run_repo, task_repo, stage_recorder):
        """When error is a plain string (not dict), it should still work."""
        workflow_repo.save(self._make_min_wf())
        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
        run_repo.save(run)
        task = Task(id="task-1", run_id="run-1", node_id="n1", status=TaskStatus.RUNNING)
        task_repo.save(task)

        engine = _build_real_engine(task_repo, run_repo)

        uc = FailTaskUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_recorder,
        )
        result = await uc.execute(FailTaskCommand(
            run_id="run-1",
            task_id="task-1",
            error={"message": "raw error"},
        ))

        assert result.failed is True


# ────────────── PauseRun ──────────────


class TestPauseRunUseCase:
    @pytest.mark.asyncio
    async def test_pause_running_run(self, logger, run_repo, stage_recorder, orchestration_engine):
        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
        run_repo.save(run)

        uc = PauseRunUseCase(
            logger=logger,
            run_repo=run_repo,
            stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
        )
        result = await uc.execute(PauseRunCommand(run_id="run-1"))

        assert result.paused is True
        updated = run_repo.get_by_id("run-1")
        assert updated.status == RunStatus.PAUSED

    @pytest.mark.asyncio
    async def test_pause_missing_run(self, logger, run_repo, stage_recorder, orchestration_engine):
        uc = PauseRunUseCase(
            logger=logger,
            run_repo=run_repo,
            stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
        )
        result = await uc.execute(PauseRunCommand(run_id="missing"))

        assert result.paused is False
        assert result.error == "Run not found"

    @pytest.mark.asyncio
    async def test_pause_completed_run(self, logger, run_repo, stage_recorder, orchestration_engine):
        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.COMPLETED)
        run_repo.save(run)

        uc = PauseRunUseCase(
            logger=logger,
            run_repo=run_repo,
            stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
        )
        result = await uc.execute(PauseRunCommand(run_id="run-1"))

        assert result.paused is False
        assert result.error_code == "INVALID_RUN_STATUS"


# ────────────── ResumeRun ──────────────


class TestResumeRunUseCase:
    @pytest.mark.asyncio
    async def test_resume_paused_run(self, logger, run_repo, task_repo, workflow_repo, stage_recorder, orchestration_engine):
        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.PAUSED)
        run_repo.save(run)

        uc = ResumeRunUseCase(
            logger=logger,
            run_repo=run_repo,
            task_repo=task_repo,
            workflow_repo=workflow_repo,
            stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
        )
        result = await uc.execute(ResumeRunCommand(run_id="run-1"))

        assert result.resumed is True
        # Phase 3b.2c: status lives on the projector — verify via the
        # emitted ``RunResumed`` event rather than the aggregate.
        assert len(orchestration_engine.emitted.get("emit_run_resumed", [])) == 1

    @pytest.mark.asyncio
    async def test_resume_missing_run(self, logger, run_repo, task_repo, workflow_repo, stage_recorder, orchestration_engine):
        uc = ResumeRunUseCase(
            logger=logger,
            run_repo=run_repo,
            task_repo=task_repo,
            workflow_repo=workflow_repo,
            stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
        )
        result = await uc.execute(ResumeRunCommand(run_id="missing"))

        assert result.resumed is False
        assert result.error == "Run not found"

    @pytest.mark.asyncio
    async def test_resume_running_run_with_no_pending_tasks_errors(self, logger, run_repo, task_repo, workflow_repo, stage_recorder, orchestration_engine):
        # A RUNNING run with no PENDING tasks is inert — nothing to resume.
        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
        run_repo.save(run)

        uc = ResumeRunUseCase(
            logger=logger,
            run_repo=run_repo,
            task_repo=task_repo,
            workflow_repo=workflow_repo,
            stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
        )
        result = await uc.execute(ResumeRunCommand(run_id="run-1"))

        assert result.resumed is False
        assert result.error_code == "INVALID_RUN_STATUS"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("terminal_status", [RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED])
    async def test_resume_terminal_run_rejected(
        self, terminal_status, logger, run_repo, task_repo, workflow_repo, stage_recorder, orchestration_engine,
    ):
        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=terminal_status)
        run_repo.save(run)

        uc = ResumeRunUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo, stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
        )
        result = await uc.execute(ResumeRunCommand(run_id="run-1"))

        assert result.resumed is False
        assert result.error_code == "INVALID_RUN_STATUS"
        assert "terminal" in result.error_details.get("reason", "")

    @pytest.mark.asyncio
    async def test_resume_fans_out_to_pending_task(
        self, logger, run_repo, task_repo, workflow_repo, stage_recorder, orchestration_engine,
    ):
        # External caller hits POST /runs/{id}/resume with updated_input — the
        # waiting INPUT task gets resumed via the delegate.
        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.PENDING)
        run_repo.save(run)
        task_repo.save(Task(id="t1", run_id="run-1", node_id="input-node", status=TaskStatus.PENDING))

        stub = _StubResumePendingTaskUseCase()
        uc = ResumeRunUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo, stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
            resume_pending_task_usecase=stub,
        )

        result = await uc.execute(ResumeRunCommand(
            run_id="run-1", updated_input={"approved": True},
        ))

        assert result.resumed is True
        assert result.resumed_task_ids == ["t1"]
        assert result.unpaused is False
        assert len(stub.calls) == 1
        assert stub.calls[0].task_id == "t1"
        assert stub.calls[0].updated_input == {"approved": True}

    @pytest.mark.asyncio
    async def test_resume_fans_out_to_all_parallel_pending_tasks(
        self, logger, run_repo, task_repo, workflow_repo, stage_recorder, orchestration_engine,
    ):
        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.PENDING)
        run_repo.save(run)
        task_repo.save(Task(id="t1", run_id="run-1", node_id="branch-a", status=TaskStatus.PENDING))
        task_repo.save(Task(id="t2", run_id="run-1", node_id="branch-b", status=TaskStatus.PENDING))
        # A non-PENDING task on the same run must be ignored.
        task_repo.save(Task(id="t3", run_id="run-1", node_id="other", status=TaskStatus.COMPLETED))

        stub = _StubResumePendingTaskUseCase()
        uc = ResumeRunUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo, stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
            resume_pending_task_usecase=stub,
        )

        result = await uc.execute(ResumeRunCommand(run_id="run-1"))

        assert result.resumed is True
        assert sorted(result.resumed_task_ids) == ["t1", "t2"]
        assert {c.task_id for c in stub.calls} == {"t1", "t2"}

    @pytest.mark.asyncio
    async def test_resume_walks_into_child_runs(
        self, logger, run_repo, task_repo, workflow_repo, stage_recorder, orchestration_engine,
    ):
        # PENDING task lives in a nested sub-workflow; resume by root run_id alone.
        parent = WorkflowRun(
            id="parent", workflow_id="wf-1", status=RunStatus.RUNNING,
            context=RunContext(child_runs={"subflow-node": "child"}),
        )
        child = WorkflowRun(
            id="child", workflow_id="wf-2", status=RunStatus.PENDING,
            parent_run_id="parent", parent_node_id="subflow-node", depth_level=1,
        )
        run_repo.save(parent)
        run_repo.save(child)
        task_repo.save(Task(id="t-child", run_id="child", node_id="input", status=TaskStatus.PENDING))

        stub = _StubResumePendingTaskUseCase()
        uc = ResumeRunUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo, stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
            resume_pending_task_usecase=stub,
        )

        result = await uc.execute(ResumeRunCommand(run_id="parent", updated_input={"x": 1}))

        assert result.resumed is True
        assert result.resumed_task_ids == ["t-child"]
        assert stub.calls[0].run_id == "child"
        assert stub.calls[0].updated_input == {"x": 1}

    @pytest.mark.asyncio
    async def test_resume_paused_run_also_resumes_pending_tasks(
        self, logger, run_repo, task_repo, workflow_repo, stage_recorder, orchestration_engine,
    ):
        # A paused run can also be waiting at an interactive node — unpause AND fan out.
        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.PAUSED)
        run_repo.save(run)
        task_repo.save(Task(id="t1", run_id="run-1", node_id="input", status=TaskStatus.PENDING))

        stub = _StubResumePendingTaskUseCase()
        uc = ResumeRunUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo, stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
            resume_pending_task_usecase=stub,
        )

        result = await uc.execute(ResumeRunCommand(run_id="run-1"))

        assert result.resumed is True
        assert result.unpaused is True
        assert result.resumed_task_ids == ["t1"]
        assert run_repo.get_by_id("run-1").status == RunStatus.RUNNING

    @pytest.mark.asyncio
    async def test_resume_logs_when_delegate_rejects_pending_task(
        self, logger, run_repo, task_repo, workflow_repo, stage_recorder, orchestration_engine,
    ):
        # If the delegate refuses (e.g. invalid task status), the run-level resume
        # surfaces the warning and excludes the task from resumed_task_ids.
        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.PENDING)
        run_repo.save(run)
        task_repo.save(Task(id="t1", run_id="run-1", node_id="input", status=TaskStatus.PENDING))

        stub = _StubResumePendingTaskUseCase(fail_task_ids={"t1"})
        uc = ResumeRunUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo, stage_recorder=stage_recorder,
            orchestration_engine=orchestration_engine,
            resume_pending_task_usecase=stub,
        )

        result = await uc.execute(ResumeRunCommand(run_id="run-1"))

        # No tasks resumed and the run wasn't paused → error.
        assert result.resumed is False
        assert result.resumed_task_ids == []
        assert result.error_code == "INVALID_RUN_STATUS"


class _StubResumePendingTaskUseCase:
    """Records delegate calls so the orchestrating use case can be tested in isolation."""

    def __init__(self, fail_task_ids: set[str] | None = None):
        self.calls: list = []
        self.fail_task_ids: set[str] = fail_task_ids or set()

    async def execute(self, command):
        from app.layer2_application.features.resume_pending_task.use_cases.resume_pending_task_use_case import (
            ResumePendingTaskResult,
        )
        self.calls.append(command)
        if command.task_id in self.fail_task_ids:
            return ResumePendingTaskResult(
                resumed=False,
                task_id=command.task_id,
                error="stubbed failure",
                error_code="INVALID_TASK_STATUS",
            )
        return ResumePendingTaskResult(
            resumed=True,
            task_id=command.task_id,
            status="COMPLETED",
        )


# ────────────── ReplayPendingRequests ──────────────


class _StubRerequestEngine:
    """Stands in for OrchestrationEngine in replay-pending-requests tests.

    Records calls to rerequest_pending_node and returns a NodeHandlerResult
    that contains a freshly-created PENDING task on the same node.
    """

    def __init__(self, workflow_repo=None):
        self.calls: list = []
        self.events_published: list = []
        self._workflow_repo = workflow_repo

    def load_workflow_for_run(self, run):
        if self._workflow_repo is None:
            return None
        return self._workflow_repo.get_by_id(run.workflow_id)

    async def rerequest_pending_node(self, node, run, workflow, topology):
        from app.layer1_domain.entities.task import Task
        from app.layer1_domain.value_objects.task_status import TaskStatus
        from app.layer2_application.node_handlers.base import NodeHandlerResult

        new_task = Task(
            run_id=run.id,
            node_id=node.id,
            status=TaskStatus.PENDING,
            output_metadata={"node_type": node.node_type.value},
        )
        self.calls.append({
            "run_id": run.id,
            "node_id": node.id,
            "new_task_id": new_task.id,
        })
        result = NodeHandlerResult()
        result.tasks_to_dispatch.append(new_task)
        return result

    def publish_event(self, topic, event):
        self.events_published.append((topic, event))

    def publish_events(self, events):
        self.events_published.extend(events)

    async def drain_events(self):
        pass


def _make_workflow_with_input_node(input_node_id="input"):
    from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
    from app.layer1_domain.value_objects.node_type import NodeType

    return Workflow(
        id="wf-1",
        name="With Input",
        nodes=[
            WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(id=input_node_id, name="Input", node_type=NodeType.INPUT),
            WorkflowNode(id="task-after", name="After", node_type=NodeType.TASK, worker_type="w1"),
        ],
        edges=[
            WorkflowEdge(source_node_id="trigger", target_node_id=input_node_id),
            WorkflowEdge(source_node_id=input_node_id, target_node_id="task-after"),
        ],
    )


class TestReplayPendingRequestsUseCase:
    @pytest.mark.asyncio
    async def test_run_not_found(
        self, logger, run_repo, task_repo, workflow_repo, stage_recorder,
    ):
        from app.layer2_application.features.replay_pending_requests.use_cases.replay_pending_requests_use_case import (
            ReplayPendingRequestsCommand,
            ReplayPendingRequestsUseCase,
        )
        uc = ReplayPendingRequestsUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo, stage_recorder=stage_recorder,
            orchestration_engine=_StubRerequestEngine(workflow_repo=workflow_repo),
        )
        result = await uc.execute(ReplayPendingRequestsCommand(run_id="missing"))
        assert result.replayed is False
        assert result.error_code == "RUN_NOT_FOUND"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("terminal_status", [RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED])
    async def test_terminal_run_rejected(
        self, terminal_status, logger, run_repo, task_repo, workflow_repo, stage_recorder,
    ):
        from app.layer2_application.features.replay_pending_requests.use_cases.replay_pending_requests_use_case import (
            ReplayPendingRequestsCommand,
            ReplayPendingRequestsUseCase,
        )
        run_repo.save(WorkflowRun(id="run-1", workflow_id="wf-1", status=terminal_status))
        uc = ReplayPendingRequestsUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo, stage_recorder=stage_recorder,
            orchestration_engine=_StubRerequestEngine(workflow_repo=workflow_repo),
        )
        result = await uc.execute(ReplayPendingRequestsCommand(run_id="run-1"))
        assert result.replayed is False
        assert result.error_code == "INVALID_RUN_STATUS"

    @pytest.mark.asyncio
    async def test_replays_single_pending_input_task(
        self, logger, run_repo, task_repo, workflow_repo, stage_recorder,
    ):
        """Old PENDING task is cancelled with reason='replayed' and engine.rerequest_pending_node fires."""
        from app.layer2_application.features.replay_pending_requests.use_cases.replay_pending_requests_use_case import (
            ReplayPendingRequestsCommand,
            ReplayPendingRequestsUseCase,
        )
        workflow_repo.save(_make_workflow_with_input_node())
        run_repo.save(WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING))
        task_repo.save(Task(
            id="t-old", run_id="run-1", node_id="input",
            status=TaskStatus.PENDING,
            output_metadata={"input_schema": [{"key": "name"}], "instruction": "fill in"},
        ))

        engine = _StubRerequestEngine(workflow_repo=workflow_repo)
        uc = ReplayPendingRequestsUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo, stage_recorder=stage_recorder,
            orchestration_engine=engine,
        )

        result = await uc.execute(ReplayPendingRequestsCommand(run_id="run-1"))

        assert result.replayed is True
        assert len(result.entries) == 1
        entry = result.entries[0]
        assert entry["run_id"] == "run-1"
        assert entry["node_id"] == "input"
        assert entry["node_type"] == "INPUT"
        assert entry["old_task_id"] == "t-old"
        assert entry["new_task_id"]
        assert entry["new_task_id"] != "t-old"

        # Old task is cancelled with the replayed marker.
        old = task_repo.get_by_id("t-old")
        assert old.status == TaskStatus.CANCELLED
        assert old.output_metadata.get("reason") == "replayed"
        # Original schema is preserved on the cancelled task.
        assert old.output_metadata.get("input_schema") == [{"key": "name"}]
        # Engine got the re-request call.
        assert len(engine.calls) == 1
        assert engine.calls[0]["node_id"] == "input"

    @pytest.mark.asyncio
    async def test_skips_non_interactive_pending_tasks(
        self, logger, run_repo, task_repo, workflow_repo, stage_recorder,
    ):
        """A PENDING task on a TASK node should NOT be replayed — only PENDING_NODE_TYPES."""
        from app.layer2_application.features.replay_pending_requests.use_cases.replay_pending_requests_use_case import (
            ReplayPendingRequestsCommand,
            ReplayPendingRequestsUseCase,
        )
        workflow_repo.save(_make_workflow_with_input_node())
        run_repo.save(WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING))
        # Task on a regular TASK node — not interactive.
        task_repo.save(Task(id="t-task", run_id="run-1", node_id="task-after", status=TaskStatus.PENDING))

        engine = _StubRerequestEngine(workflow_repo=workflow_repo)
        uc = ReplayPendingRequestsUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo, stage_recorder=stage_recorder,
            orchestration_engine=engine,
        )

        result = await uc.execute(ReplayPendingRequestsCommand(run_id="run-1"))

        assert result.replayed is False
        assert result.entries == []
        assert engine.calls == []
        # Task untouched.
        assert task_repo.get_by_id("t-task").status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_walks_into_child_runs(
        self, logger, run_repo, task_repo, workflow_repo, stage_recorder,
    ):
        from app.layer2_application.features.replay_pending_requests.use_cases.replay_pending_requests_use_case import (
            ReplayPendingRequestsCommand,
            ReplayPendingRequestsUseCase,
        )
        workflow_repo.save(_make_workflow_with_input_node())
        # Child workflow has its own input node.
        workflow_repo.save(_make_workflow_with_input_node())  # reuses wf-1 — fine for stub
        parent = WorkflowRun(
            id="parent", workflow_id="wf-1", status=RunStatus.RUNNING,
            context=RunContext(child_runs={"sub": "child"}),
        )
        child = WorkflowRun(
            id="child", workflow_id="wf-1", status=RunStatus.RUNNING,
            parent_run_id="parent", parent_node_id="sub", depth_level=1,
        )
        run_repo.save(parent)
        run_repo.save(child)
        task_repo.save(Task(id="t-child", run_id="child", node_id="input", status=TaskStatus.PENDING))

        engine = _StubRerequestEngine(workflow_repo=workflow_repo)
        uc = ReplayPendingRequestsUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo, stage_recorder=stage_recorder,
            orchestration_engine=engine,
        )

        result = await uc.execute(ReplayPendingRequestsCommand(run_id="parent"))

        assert result.replayed is True
        assert len(result.entries) == 1
        assert result.entries[0]["run_id"] == "child"
        assert result.entries[0]["old_task_id"] == "t-child"
        assert engine.calls[0]["run_id"] == "child"

    @pytest.mark.asyncio
    async def test_no_pending_tasks_returns_replayed_false(
        self, logger, run_repo, task_repo, workflow_repo, stage_recorder,
    ):
        from app.layer2_application.features.replay_pending_requests.use_cases.replay_pending_requests_use_case import (
            ReplayPendingRequestsCommand,
            ReplayPendingRequestsUseCase,
        )
        workflow_repo.save(_make_workflow_with_input_node())
        run_repo.save(WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING))

        uc = ReplayPendingRequestsUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo, stage_recorder=stage_recorder,
            orchestration_engine=_StubRerequestEngine(workflow_repo=workflow_repo),
        )

        result = await uc.execute(ReplayPendingRequestsCommand(run_id="run-1"))
        # Nothing to replay — not an error, just an empty result.
        assert result.replayed is False
        assert result.entries == []
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_records_stage_event_on_cancel(
        self, logger, run_repo, task_repo, workflow_repo, stage_recorder,
    ):
        from app.layer2_application.features.replay_pending_requests.use_cases.replay_pending_requests_use_case import (
            ReplayPendingRequestsCommand,
            ReplayPendingRequestsUseCase,
        )
        workflow_repo.save(_make_workflow_with_input_node())
        run_repo.save(WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING))
        task_repo.save(Task(id="t-old", run_id="run-1", node_id="input", status=TaskStatus.PENDING))

        uc = ReplayPendingRequestsUseCase(
            logger=logger, run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo, stage_recorder=stage_recorder,
            orchestration_engine=_StubRerequestEngine(workflow_repo=workflow_repo),
        )

        await uc.execute(ReplayPendingRequestsCommand(run_id="run-1"))

        cancel_records = [r for r in stage_recorder.records if r.get("event_type") == "node.cancelled"]
        assert len(cancel_records) == 1
        assert cancel_records[0]["task_id"] == "t-old"
        assert cancel_records[0]["metadata"]["reason"] == "replay"


# ────────────── UpdateWorkflow ──────────────


class TestUpdateWorkflowUseCase:
    @pytest.mark.asyncio
    async def test_update_name(self, logger, workflow_repo, sample_workflow):
        workflow_repo.save(sample_workflow)
        uc = UpdateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        result = await uc.execute(UpdateWorkflowCommand(workflow_id="wf-1", name="Updated Name"))

        assert result.name == "Updated Name"
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_update_missing_workflow(self, logger, workflow_repo):
        uc = UpdateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        result = await uc.execute(UpdateWorkflowCommand(workflow_id="missing", name="X"))

        assert result.error == "Workflow not found"

    @pytest.mark.asyncio
    async def test_update_description(self, logger, workflow_repo, sample_workflow):
        workflow_repo.save(sample_workflow)
        uc = UpdateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        result = await uc.execute(UpdateWorkflowCommand(
            workflow_id="wf-1", description="New description",
        ))

        assert result.description == "New description"

    @pytest.mark.asyncio
    async def test_update_nodes_and_edges(self, logger, workflow_repo, sample_workflow):
        workflow_repo.save(sample_workflow)
        uc = UpdateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        result = await uc.execute(UpdateWorkflowCommand(
            workflow_id="wf-1",
            nodes=[
                {"id": "n1", "name": "New", "node_type": "TRIGGER"},
                {"id": "n2", "name": "End", "node_type": "TASK"},
            ],
            edges=[{"source_node_id": "n1", "target_node_id": "n2"}],
        ))

        assert len(result.nodes) == 2
        assert result.nodes[0]["name"] == "New"

    @pytest.mark.asyncio
    async def test_update_schemas(self, logger, workflow_repo, sample_workflow):
        workflow_repo.save(sample_workflow)
        uc = UpdateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        schema_list = [
            {"key": "name", "outputType": "string", "fieldConfig": {"label": "Name"}},
        ]
        result = await uc.execute(UpdateWorkflowCommand(
            workflow_id="wf-1",
            input_schema=schema_list,
            output_schema=schema_list,
        ))

        assert result.input_schema == schema_list
        assert result.output_schema == schema_list

    @pytest.mark.asyncio
    async def test_update_metadata(self, logger, workflow_repo, sample_workflow):
        workflow_repo.save(sample_workflow)
        uc = UpdateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        result = await uc.execute(UpdateWorkflowCommand(
            workflow_id="wf-1",
            metadata={"custom": "data"},
        ))

        assert result.metadata == {"custom": "data"}


# ────────────── normalize_schema ──────────────


class TestNormalizeSchema:
    def test_none_returns_empty_list(self):
        from app.layer1_domain.entities.workflow import normalize_schema
        assert normalize_schema(None) == []

    def test_empty_dict_returns_empty_list(self):
        from app.layer1_domain.entities.workflow import normalize_schema
        assert normalize_schema({}) == []

    def test_list_passthrough(self):
        from app.layer1_domain.entities.workflow import normalize_schema
        fields = [{"key": "a", "outputType": "string", "fieldConfig": {"label": "A"}}]
        assert normalize_schema(fields) == fields

    def test_dict_with_fields_key(self):
        from app.layer1_domain.entities.workflow import normalize_schema
        fields = [{"key": "a"}]
        assert normalize_schema({"fields": fields}) == fields

    def test_dict_without_fields_key(self):
        from app.layer1_domain.entities.workflow import normalize_schema
        assert normalize_schema({"type": "object"}) == []

    def test_non_dict_non_list(self):
        from app.layer1_domain.entities.workflow import normalize_schema
        assert normalize_schema("invalid") == []

    def test_dict_with_non_list_fields(self):
        from app.layer1_domain.entities.workflow import normalize_schema
        assert normalize_schema({"fields": "bad"}) == []


# ────────────── SchemaValidator ──────────────


class TestSchemaValidator:
    def test_validate_required_field_missing(self):
        from app.layer2_application.read.schema_validator import SchemaValidator
        schema = [
            {
                "key": "name",
                "outputType": "string",
                "fieldConfig": {"label": "Name"},
                "rules": {"isRequired": True},
            },
        ]
        errors = SchemaValidator.validate_input({}, schema)
        assert len(errors) == 1
        assert "Name" in errors[0]
        assert "name" in errors[0]

    def test_validate_required_field_present(self):
        from app.layer2_application.read.schema_validator import SchemaValidator
        schema = [
            {
                "key": "name",
                "outputType": "string",
                "fieldConfig": {"label": "Name"},
                "rules": {"isRequired": True},
            },
        ]
        errors = SchemaValidator.validate_input({"name": "hello"}, schema)
        assert errors == []

    def test_validate_optional_field_missing(self):
        from app.layer2_application.read.schema_validator import SchemaValidator
        schema = [
            {"key": "desc", "outputType": "string", "fieldConfig": {"label": "Description"}},
        ]
        errors = SchemaValidator.validate_input({}, schema)
        assert errors == []

    def test_validate_empty_schema(self):
        from app.layer2_application.read.schema_validator import SchemaValidator
        errors = SchemaValidator.validate_input({"any": "data"}, [])
        assert errors == []

    def test_validate_multiple_required_fields(self):
        from app.layer2_application.read.schema_validator import SchemaValidator
        schema = [
            {"key": "a", "outputType": "string", "fieldConfig": {"label": "A"}, "rules": {"isRequired": True}},
            {"key": "b", "outputType": "string", "fieldConfig": {"label": "B"}, "rules": {"isRequired": True}},
        ]
        errors = SchemaValidator.validate_input({"a": "ok"}, schema)
        assert len(errors) == 1
        assert "B" in errors[0]

    def test_validate_no_rules_key(self):
        from app.layer2_application.read.schema_validator import SchemaValidator
        schema = [{"key": "x", "outputType": "string", "fieldConfig": {"label": "X"}}]
        errors = SchemaValidator.validate_input({}, schema)
        assert errors == []


# ────────────── StartRun with input schema validation ──────────────


class TestStartRunInputValidation:
    @pytest.mark.asyncio
    async def test_start_run_rejects_missing_required_input(self, logger, workflow_repo, run_repo, task_repo):
        wf = Workflow(
            id="wf-schema",
            workflow_id="wf-schema",
            status=WorkflowStatus.PUBLISHED,
            name="Schema WF",
            input_schema=[
                {
                    "key": "url",
                    "outputType": "string",
                    "fieldConfig": {"label": "URL"},
                    "rules": {"isRequired": True},
                },
            ],
            nodes=[
                WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
                WorkflowNode(id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
            ],
            edges=[WorkflowEdge(source_node_id="t", target_node_id="task1")],
        )
        workflow_repo.save(wf)

        engine = _build_real_engine(task_repo, run_repo)
        sh_repo = MemoryStageHistoryRepository()
        stage_rec = StageRecorder(repository=sh_repo)

        uc = StartRunUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_rec,
        )
        result = await uc.execute(StartRunCommand(
            workflow_id="wf-schema",
            input_data={},
            skip_validation=False,
        ))

        assert result.error is not None
        assert result.error_code == "INPUT_VALIDATION_FAILED"
        assert any("URL" in e for e in result.error_details.get("errors", []))

    @pytest.mark.asyncio
    async def test_start_run_passes_with_valid_input(self, logger, workflow_repo, run_repo, task_repo):
        wf = Workflow(
            id="wf-schema-ok",
            workflow_id="wf-schema-ok",
            status=WorkflowStatus.PUBLISHED,
            name="Schema WF",
            input_schema=[
                {
                    "key": "url",
                    "outputType": "string",
                    "fieldConfig": {"label": "URL"},
                    "rules": {"isRequired": True},
                },
            ],
            nodes=[
                WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
                WorkflowNode(id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
            ],
            edges=[WorkflowEdge(source_node_id="t", target_node_id="task1")],
        )
        workflow_repo.save(wf)

        engine = _build_real_engine(task_repo, run_repo)
        sh_repo = MemoryStageHistoryRepository()
        stage_rec = StageRecorder(repository=sh_repo)

        uc = StartRunUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_rec,
        )
        result = await uc.execute(StartRunCommand(
            workflow_id="wf-schema-ok",
            input_data={"url": "https://example.com"},
        ))

        assert result.error is None
        assert result.status in ("RUNNING", "COMPLETED")

    @pytest.mark.asyncio
    async def test_start_run_skips_validation_when_no_schema(self, logger, workflow_repo, run_repo, task_repo):
        wf = Workflow(
            id="wf-no-schema",
            workflow_id="wf-no-schema",
            status=WorkflowStatus.PUBLISHED,
            name="No Schema WF",
            nodes=[
                WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
                WorkflowNode(id="task1", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
            ],
            edges=[WorkflowEdge(source_node_id="t", target_node_id="task1")],
        )
        workflow_repo.save(wf)

        engine = _build_real_engine(task_repo, run_repo)
        sh_repo = MemoryStageHistoryRepository()
        stage_rec = StageRecorder(repository=sh_repo)

        uc = StartRunUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_rec,
        )
        result = await uc.execute(StartRunCommand(
            workflow_id="wf-no-schema",
            input_data={},
        ))

        assert result.error is None


# ────────────── StartRun error handling ──────────────



# ────────────── Create/Get round-trip with auto-form schema ──────────────


class TestWorkflowSchemaRoundTrip:
    @pytest.mark.asyncio
    async def test_create_get_round_trip(self, logger, workflow_repo):
        auto_form_schema = [
            {
                "key": "url",
                "outputType": "string",
                "fieldConfig": {"label": "URL", "description": "Target URL", "fieldControl": "Input"},
                "rules": {"isRequired": True},
            },
            {
                "key": "timeout",
                "outputType": "number",
                "fieldConfig": {"label": "Timeout (s)"},
            },
        ]
        create_uc = CreateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        create_result = await create_uc.execute(CreateWorkflowCommand(
            name="Round Trip WF",
            input_schema=auto_form_schema,
            output_schema=[{"key": "result", "outputType": "string", "fieldConfig": {"label": "Result"}}],
        ))

        assert create_result.input_schema == auto_form_schema

        get_uc = GetWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        get_result = await get_uc.execute(GetWorkflowCommand(workflow_id=create_result.id))

        assert get_result.input_schema == auto_form_schema
        assert get_result.output_schema == [{"key": "result", "outputType": "string", "fieldConfig": {"label": "Result"}}]


class TestRunMetadataEcho:
    """Caller-supplied run metadata is persisted, seeded for expressions, and
    echoed onto the completion callback."""

    @pytest.mark.asyncio
    async def test_start_run_persists_and_seeds_metadata(
        self, logger, workflow_repo, run_repo, task_repo
    ):
        wf = Workflow(
            id="wf-md",
            workflow_id="wf-md",
            status=WorkflowStatus.PUBLISHED,
            name="Metadata WF",
            nodes=[WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER)],
            edges=[],
        )
        workflow_repo.save(wf)

        engine = _build_real_engine(task_repo, run_repo)
        stage_rec = StageRecorder(repository=MemoryStageHistoryRepository())
        uc = StartRunUseCase(
            logger=logger,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
            task_repo=task_repo,
            orchestration_engine=engine,
            stage_recorder=stage_rec,
        )

        meta = {"source_system": "crm", "trace_id": "abc-123"}
        result = await uc.execute(StartRunCommand(workflow_id="wf-md", metadata=meta))

        assert result.error is None
        saved = run_repo.get_by_id(result.id)
        assert saved.metadata == meta
        # Exposed to expressions as {{$run.metadata.X}}.
        assert saved.context.variables["run"]["metadata"] == meta

    def test_publish_callback_includes_metadata(self, run_repo, task_repo):
        from unittest.mock import MagicMock

        engine = _build_real_engine(task_repo, run_repo)
        engine._callback_publisher = MagicMock()

        run = WorkflowRun(
            id="r-md",
            workflow_id="wf",
            callback_topic="agent.callbacks",
            correlation_id="corr-1",
            output_data={"ok": True},
            metadata={"source_system": "crm"},
        )
        engine.publish_callback(run, "COMPLETED")

        engine._callback_publisher.publish.assert_called_once()
        message = engine._callback_publisher.publish.call_args[0][1]
        assert message["metadata"] == {"source_system": "crm"}
