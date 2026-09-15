"""Tests for Node Builder execution integration.

Covers:
- task_handler with locked defaults (passthrough)
- task_handler with expression-based input_mapping
- complete_task with output_mapping
- workflow save enrichment
- backward compat (no node_definition_id = no change)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.node_handlers.task_handler import TaskNodeHandler
from app.layer2_application.resolve.variable_resolver import VariableResolver, TraversalResolverStrategy
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.features.create_workflow.use_cases.create_workflow_use_case import (
    CreateWorkflowUseCase, CreateWorkflowCommand,
)
from app.layer2_application.features.update_workflow.use_cases.update_workflow_use_case import (
    UpdateWorkflowUseCase, PatchWorkflowCommand,
)
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskUseCase, CompleteTaskCommand,
)


# -- Task handler tests --


class TestTaskHandlerNodeDefinition:

    @pytest.fixture
    def resolver(self):
        return VariableResolver(strategies=[TraversalResolverStrategy()])

    @pytest.fixture
    def handler(self, resolver):
        return TaskNodeHandler(resolver)

    @pytest.fixture
    def task_factory(self):
        return TaskFactory()

    @pytest.fixture
    def run(self):
        return WorkflowRun(
            id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING,
            context=RunContext(variables={"prev_node": {"data": "hello"}}),
        )

    @pytest.fixture
    def topology(self):
        return GraphTopology()

    async def test_locked_defaults_passthrough(self, handler, run, topology, task_factory):
        """Locked defaults are merged into resolved input."""
        node = WorkflowNode(
            id="n1", name="Task1", node_type=NodeType.TASK, worker_type="w1",
            input_binding={"custom_key": "user_value"},
            definition_snapshot={
                "locked_defaults": {"method": "GET", "url": "https://api/orders"},
                "input_mapping": {},
                "output_mapping": {},
                "base_worker_id": "w1",
            },
        )
        workflow = Workflow(id="wf-1", name="W", nodes=[node])
        result = await handler.handle(node, run, workflow, topology, None, task_factory)
        task = result.tasks_to_dispatch[0]
        assert task.resolved_input["method"] == "GET"
        assert task.resolved_input["url"] == "https://api/orders"
        assert task.resolved_input["custom_key"] == "user_value"

    async def test_locked_defaults_override_custom(self, handler, run, topology, task_factory):
        """Locked defaults always win over user-provided input."""
        node = WorkflowNode(
            id="n1", name="Task1", node_type=NodeType.TASK, worker_type="w1",
            input_binding={"method": "DELETE"},
            definition_snapshot={
                "locked_defaults": {"method": "GET"},
                "input_mapping": {},
                "output_mapping": {},
                "base_worker_id": "w1",
            },
        )
        workflow = Workflow(id="wf-1", name="W", nodes=[node])
        result = await handler.handle(node, run, workflow, topology, None, task_factory)
        task = result.tasks_to_dispatch[0]
        assert task.resolved_input["method"] == "GET"

    async def test_input_mapping_expression(self, handler, run, topology, task_factory):
        """input_mapping resolves expressions from run context + custom input."""
        node = WorkflowNode(
            id="n1", name="Task1", node_type=NodeType.TASK, worker_type="w1",
            input_binding={"customer_id": "C123"},
            definition_snapshot={
                "locked_defaults": {"method": "GET"},
                "input_mapping": {"data_ref": "{{$prev_node.data}}"},
                "output_mapping": {},
                "base_worker_id": "w1",
            },
        )
        workflow = Workflow(id="wf-1", name="W", nodes=[node])
        result = await handler.handle(node, run, workflow, topology, None, task_factory)
        task = result.tasks_to_dispatch[0]
        assert task.resolved_input["data_ref"] == "hello"
        assert task.resolved_input["method"] == "GET"
        assert task.resolved_input["customer_id"] == "C123"

    async def test_no_node_definition_backward_compat(self, handler, run, topology, task_factory):
        """Without _node_definition, handler works as before."""
        node = WorkflowNode(
            id="n1", name="Task1", node_type=NodeType.TASK, worker_type="w1",
            input_binding={"key": "value"},
        )
        workflow = Workflow(id="wf-1", name="W", nodes=[node])
        result = await handler.handle(node, run, workflow, topology, None, task_factory)
        task = result.tasks_to_dispatch[0]
        assert task.resolved_input == {"key": "value"}


# -- Workflow save enrichment tests --


class TestWorkflowSaveEnrichment:

    async def test_create_workflow_enriches_nodes(self, logger, workflow_repo, node_definition_repo):
        nd = NodeDefinition(
            id="nd-1", tenant_id="t1", name="Get Orders",
            base_type=NodeType.TASK, base_worker_id="http-worker",
            locked_defaults={"method": "GET"},
            input_mapping={"url_param": "{{$custom.id}}"},
            output_mapping={"items": "{{$_output.body}}"},
            data_mode="csv_single",
            edit_mode="before_dispatch",
        )
        node_definition_repo.save(nd)

        uc = CreateWorkflowUseCase(
            logger=logger, workflow_repo=workflow_repo,
            node_definition_repo=node_definition_repo,
        )
        result = await uc.execute(CreateWorkflowCommand(
            name="Test WF",
            nodes=[{"id": "n1", "name": "N1", "node_type": "TASK", "node_definition_id": "nd-1"}],
        ))
        assert result.id
        wf = workflow_repo.get_by_id(result.id)
        node = wf.nodes[0]
        assert node.worker_type == "http-worker"
        snap = node.definition_snapshot
        assert snap["data_mode"] == "csv_single"
        assert snap["edit_mode"] == "before_dispatch"
        assert snap["input_schema"] == []
        assert snap["locked_defaults"] == {"method": "GET"}
        assert snap["input_mapping"] == {"url_param": "{{$custom.id}}"}
        assert snap["output_mapping"] == {"items": "{{$_output.body}}"}
        assert snap["base_worker_id"] == "http-worker"
        # Nothing is smuggled into config any more (NodeSpec Phase 4).
        assert "_node_definition" not in node.config
        assert "_node_internals" not in node.config

    async def test_create_workflow_no_definition_id_untouched(self, logger, workflow_repo, node_definition_repo):
        uc = CreateWorkflowUseCase(
            logger=logger, workflow_repo=workflow_repo,
            node_definition_repo=node_definition_repo,
        )
        result = await uc.execute(CreateWorkflowCommand(
            name="Plain WF",
            nodes=[{"id": "n1", "name": "N1", "node_type": "TASK", "worker_type": "w1"}],
        ))
        wf = workflow_repo.get_by_id(result.id)
        node = wf.nodes[0]
        assert node.definition_snapshot is None

    async def test_update_workflow_enriches_nodes(self, logger, workflow_repo, node_definition_repo):
        nd = NodeDefinition(
            id="nd-2", tenant_id="t1", name="Notify",
            base_type=NodeType.TASK, base_worker_id="email-worker",
            locked_defaults={"template": "welcome"},
        )
        node_definition_repo.save(nd)

        wf = Workflow(id="wf-up", name="WF")
        workflow_repo.save(wf)

        uc = UpdateWorkflowUseCase(
            logger=logger, workflow_repo=workflow_repo,
            node_definition_repo=node_definition_repo,
        )
        result = await uc.execute(PatchWorkflowCommand(
            workflow_id="wf-up",
            nodes=[{"id": "n1", "name": "N1", "node_type": "TASK", "node_definition_id": "nd-2"}],
        ))
        wf = workflow_repo.get_by_id("wf-up")
        node = wf.nodes[0]
        assert node.worker_type == "email-worker"
        assert node.definition_snapshot["locked_defaults"] == {"template": "welcome"}

    async def test_update_workflow_enriches_business_worker_type(
        self, logger, workflow_repo, node_definition_repo
    ):
        """A node that carries the definition only via a ``business:<id>``
        worker_type (FE palette drop, no explicit node_definition_id) must
        still be enriched: id derived, snapshot populated, and the catalog
        worker_type normalized to the runnable base worker."""
        nd = NodeDefinition(
            id="nd-biz", tenant_id="t1", name="Array Transform",
            base_type=NodeType.TASK, base_worker_id="code",
            locked_defaults={"code": "return 1", "language": "python"},
        )
        node_definition_repo.save(nd)

        wf = Workflow(id="wf-biz", name="WF")
        workflow_repo.save(wf)

        uc = UpdateWorkflowUseCase(
            logger=logger, workflow_repo=workflow_repo,
            node_definition_repo=node_definition_repo,
        )
        await uc.execute(PatchWorkflowCommand(
            workflow_id="wf-biz",
            nodes=[{
                "id": "n1", "name": "N1", "node_type": "TASK",
                "worker_type": "business:nd-biz",
            }],
        ))
        wf = workflow_repo.get_by_id("wf-biz")
        node = wf.nodes[0]
        assert node.node_definition_id == "nd-biz"
        assert node.worker_type == "code"
        assert node.definition_snapshot["locked_defaults"] == {
            "code": "return 1", "language": "python",
        }


# -- Complete task output mapping tests --


class TestCompleteTaskOutputMapping:

    @pytest.fixture
    def mock_engine(self, workflow_repo):
        from app.layer2_application.node_handlers.base import NodeHandlerResult
        engine = MagicMock()
        engine.process_successors = AsyncMock(return_value=NodeHandlerResult())
        engine.process_and_dispatch_successors = AsyncMock(return_value=NodeHandlerResult())
        engine.check_run_completion = AsyncMock()
        engine.publish_event = MagicMock()
        engine.publish_events = MagicMock()
        engine.drain_events = AsyncMock()
        engine.drain_events = AsyncMock()
        engine.is_builtin_node = MagicMock(return_value=False)
        engine.maybe_enqueue_loop_iteration = AsyncMock()
        engine._queue = MagicMock()
        engine._queue.enqueue = AsyncMock()
        # Phase B+C: use cases resolve workflow via the engine. Wire
        # the test's workflow_repo so the mock returns a real Workflow.
        engine.load_workflow_for_run = lambda run: workflow_repo.get_by_id(run.workflow_id)
        # Phase 1a engine-split: complete_task_use_case routes writes
        # through apply_state_updates_as_files. Mirror the engine's
        # mutation for the mock so the test's assertions on
        # ``run.context.variables`` still hold.
        async def _apply(
            state_updates, run, workflow,
            *, source_node_id="", causation_id="",
            output_port="", taken_edge_ports=None, attempt_id="", task=None,
        ):
            for key, value in (state_updates or {}).items():
                run.context.variables[key] = value
                try:
                    node = workflow.get_node(key)
                    if node and node.name and node.name != key:
                        run.context.variables[node.name] = value
                except Exception:
                    pass
        engine.apply_state_updates_as_files = _apply
        return engine

    async def test_output_mapping_applied(self, logger, workflow_repo, run_repo, task_repo,
                                          stage_recorder, mock_engine):
        node = WorkflowNode(
            id="n1", name="Task1", node_type=NodeType.TASK, worker_type="w1",
            definition_snapshot={
                "locked_defaults": {},
                "input_mapping": {},
                "output_mapping": {"result_key": "{{$_output.metadata.val}}"},
                "base_worker_id": "w1",
            },
        )
        wf = Workflow(id="wf-1", name="W", nodes=[node])
        workflow_repo.save(wf)

        run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
        run_repo.save(run)

        task = Task(id="task-1", run_id="run-1", node_id="n1", status=TaskStatus.RUNNING, worker_type="w1")
        task_repo.save(task)

        resolver = VariableResolver(strategies=[TraversalResolverStrategy()])
        uc = CompleteTaskUseCase(
            logger=logger, workflow_repo=workflow_repo,
            run_repo=run_repo, task_repo=task_repo,
            orchestration_engine=mock_engine,
            stage_recorder=stage_recorder,
            variable_resolver=resolver,
        )
        result = await uc.execute(CompleteTaskCommand(
            run_id="run-1", task_id="task-1",
            output_reference="ref-1",
            output_metadata={"val": "mapped_value"},
        ))
        assert result.completed is True
        saved_run = run_repo.get_by_id("run-1")
        task_output = saved_run.context.variables["n1"]
        assert task_output.get("result_key") == "mapped_value"

    async def test_no_output_mapping_backward_compat(self, logger, workflow_repo, run_repo, task_repo,
                                                      stage_recorder, mock_engine):
        node = WorkflowNode(id="n1", name="Task1", node_type=NodeType.TASK, worker_type="w1")
        wf = Workflow(id="wf-2", name="W", nodes=[node])
        workflow_repo.save(wf)

        run = WorkflowRun(id="run-2", workflow_id="wf-2", status=RunStatus.RUNNING)
        run_repo.save(run)

        task = Task(id="task-2", run_id="run-2", node_id="n1", status=TaskStatus.RUNNING, worker_type="w1")
        task_repo.save(task)

        uc = CompleteTaskUseCase(
            logger=logger, workflow_repo=workflow_repo,
            run_repo=run_repo, task_repo=task_repo,
            orchestration_engine=mock_engine,
            stage_recorder=stage_recorder,
        )
        result = await uc.execute(CompleteTaskCommand(
            run_id="run-2", task_id="task-2",
            output_reference="ref-2",
        ))
        assert result.completed is True
        saved_run = run_repo.get_by_id("run-2")
        task_output = saved_run.context.variables["n1"]
        assert "output" in task_output
