"""Regression test for the queued BUILTIN state-updates bug (2026-04-11).

Covers the bug reported in ``docs/design/child-workflow-input-bug-analysis.md``:
``OrchestrationEngine._exec_builtin_queued`` (line 1463) and
``OrchestrationEngine.orchestrate_loop_iteration`` (line 1755) were calling
``self.apply_state_updates_as_files(...)`` without ``await``. The method is
``async def`` so the call silently returned a coroutine that was never
awaited, and BUILTIN handlers' ``state_updates`` were dropped on the floor.

The bug was invisible to the existing suite because every test ran without
an orchestration queue configured, meaning ``_enqueue_or_dispatch`` always
took the inline fallback branch instead of the queued path.

This test wires a ``MemoryOrchestrationQueue`` so
``_enqueue_or_dispatch`` follows the queued path, then directly invokes
``orchestrate_execute_node`` (the entry point the worker would call) and
asserts that ``run.context.variables`` ends up populated by the BUILTIN
handler's state updates.

Previously, without the ``await``, the assertion
``is_file_ref(compute_output)`` would fail because
``run.context.variables[compute_node_id]`` would be missing entirely.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import GraphTopology, RunContext, WorkflowRun
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
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
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer2_application.orchestration_queue.memory_queue import (
    MemoryOrchestrationQueue,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from tests.helpers.event_capture import CapturingEventPublisher


class _DummyLogger:
    def info(self, *a, **kw): ...
    def warning(self, *a, **kw): ...
    def error(self, *a, **kw): ...
    def debug(self, *a, **kw): ...


def _build_trigger_compute_workflow() -> Workflow:
    """TRIGGER → COMPUTE. Both are BUILTIN nodes.

    The compute node has a constant expression (no upstream references)
    so resolution cannot fail — the test is only concerned with whether
    ``state_updates`` are applied after the handler runs via the queued
    dispatch path.
    """
    trigger = WorkflowNode(
        id="trigger-1",
        name="trigger",
        node_type=NodeType.TRIGGER,
        config={},
    )
    compute = WorkflowNode(
        id="compute-1",
        name="double_seven",
        node_type=NodeType.COMPUTE,
        config={"expression": "7 * 2"},
    )
    return Workflow(
        id="wf-queued-builtin-1",
        name="queued-builtin-regression",
        version="1",
        nodes=[trigger, compute],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="compute-1"),
        ],
    )


@pytest.fixture
def engine_with_queue():
    """Build an OrchestrationEngine wired with a ``MemoryOrchestrationQueue``.

    This is the only test fixture in the suite that exercises the queued
    dispatch path. All other tests run with ``queue=None`` which takes
    the inline fallback and does not hit ``_exec_builtin_queued`` /
    ``_exec_pending_queued`` / ``_exec_task_queued`` /
    ``orchestrate_loop_iteration``.
    """
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

    handler_registry = NodeHandlerRegistry()
    handler_registry.register(NodeType.TRIGGER, TriggerNodeHandler())
    handler_registry.register(NodeType.COMPUTE, ComputeNodeHandler(resolver))

    task_factory = TaskFactory()
    successor_processor = SuccessorProcessor(
        handler_registry, task_factory, data_store_service=data_store,
    )

    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    workflow_repo = MemoryWorkflowRepository()

    stage_recorder = StageRecorder(MagicMock())
    stage_recorder.record = MagicMock()

    event_publisher = CapturingEventPublisher()
    engine = OrchestrationEngine(
        handler_registry=handler_registry,
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
    )

    # Wire the memory queue — this is what makes this test cover the queued
    # dispatch path that Phase 0's async-conversion replace_all missed when
    # the linter introduced _exec_builtin_queued after Phase 0 had already run.
    queue = MemoryOrchestrationQueue()
    engine.set_queue(queue)

    return {
        "engine": engine,
        "queue": queue,
        "workflow_repo": workflow_repo,
        "run_repo": run_repo,
        "task_repo": task_repo,
        "task_factory": task_factory,
        "data_store": data_store,
        "file_client": file_client,
        "event_publisher": event_publisher,
    }


class TestQueuedBuiltinStateUpdates:
    async def test_compute_state_updates_applied_via_queued_exec(self, engine_with_queue, recwarn):
        """BUILTIN handler's state_updates must land in run.context.variables
        when dispatched via the queued path. Before the fix the coroutine
        returned by apply_state_updates_as_files was discarded."""
        deps = engine_with_queue
        engine = deps["engine"]

        workflow = _build_trigger_compute_workflow()
        deps["workflow_repo"].save(workflow)

        topology = GraphTopology.from_workflow(workflow)
        run = WorkflowRun(
            id="run-queued-1",
            workflow_id=workflow.id,
            status=RunStatus.RUNNING,
            context=RunContext(
                # TRIGGER is COMPUTE's predecessor — mark it completed so the
                # Fix B predecessor-readiness guard in _execute_node_inner
                # lets COMPUTE through. Without this the guard would
                # correctly defer COMPUTE and the test would exercise the
                # guard instead of the state-updates path.
                completed_nodes={"trigger-1"},
                variables={},
                topology=topology,
            ),
        )
        deps["run_repo"].save(run)

        # Build a READY task for the COMPUTE node directly (skip the TRIGGER
        # fan-out; we're testing the single-node queued execution path).
        compute_node = workflow.get_node("compute-1")
        compute_task = deps["task_factory"].create_task(compute_node, run)
        deps["task_repo"].save(compute_task)

        # Drive the queued path exactly as the worker would.
        await engine.orchestrate_execute_node(run.id, compute_task.id)

        # Phase 3b.1: ``apply_state_updates_as_files`` no longer mutates
        # the aggregate — the state-updates write-set now travels via
        # ``NodeOutputProduced``. Assert that the event was emitted with
        # the expected payload (the projector's input contract). A dropped
        # coroutine would produce zero events here, which is the original
        # bug this test guards against.
        projected = deps["event_publisher"].projected_variables()
        assert "compute-1" in projected, (
            "compute-1 should be present in the NodeOutputProduced write-set "
            "after _exec_builtin_queued runs. If this assertion fails, the "
            "regression at orchestration_engine.py:1463 has returned — "
            "apply_state_updates_as_files is being called without await."
        )

        compute_output = projected["compute-1"]
        # state_updates are wrapped by apply_state_updates_as_files → _to_file_ref
        # → data_store_service.store_data, producing a file_ref dict.
        assert is_file_ref(compute_output), (
            f"compute output should be a file_ref; got {type(compute_output).__name__}"
        )

        # Verify the computed value (7 * 2 = 14) round-trips through the
        # file-backed output. No-``extracted`` model: the value lives in the CSV,
        # not inline on the ref, so materialize to read it back.
        materialized = await deps["data_store"].materialize(compute_output)
        assert materialized["output"]["result"] == 14

        # Also assert the compute node was added to completed_nodes and the
        # task itself was marked COMPLETED — those are the other side effects
        # of _exec_builtin_queued's happy path.
        saved_run = deps["run_repo"].get_by_id(run.id)
        assert "compute-1" in saved_run.context.completed_nodes
        saved_task = deps["task_repo"].get_by_id(compute_task.id)
        assert saved_task.status == TaskStatus.COMPLETED

        # Sanity: no "coroutine was never awaited" warning should have been
        # emitted during the test. Python issues this warning at GC time for
        # any coroutine created without being awaited.
        awaited_warnings = [
            w for w in recwarn.list
            if "was never awaited" in str(w.message)
            and "apply_state_updates_as_files" in str(w.message)
        ]
        assert not awaited_warnings, (
            f"apply_state_updates_as_files coroutine was not awaited: {awaited_warnings}"
        )

    async def test_enqueued_task_executes_without_and_join_guard(self, engine_with_queue):
        """Edge-based dispatch contract (supersedes the old Fix B guard).

        The predecessor-readiness AND-join guard in
        ``_execute_node_inner`` was RETIRED (orchestration_engine.py:2153):
        a node is only enqueued when one of its incoming edges is taken,
        so readiness is enforced at dispatch time, not execution time.
        An explicitly enqueued task therefore EXECUTES — there is no
        execution-time deferral. (MERGE remains the AND-join: its handler
        tracks quorum and returns ``deferred`` until the strategy fires.)

        This test documents the retirement as a tripwire: if an
        execution-time guard is reintroduced, review it against the
        edge-based dispatch design before changing this test.
        """
        deps = engine_with_queue
        engine = deps["engine"]

        workflow = _build_trigger_compute_workflow()
        deps["workflow_repo"].save(workflow)

        topology = GraphTopology.from_workflow(workflow)
        # Deliberately do NOT add "trigger-1" to completed_nodes.
        run = WorkflowRun(
            id="run-queued-guard-1",
            workflow_id=workflow.id,
            status=RunStatus.RUNNING,
            context=RunContext(variables={}, topology=topology),
        )
        deps["run_repo"].save(run)

        compute_node = workflow.get_node("compute-1")
        compute_task = deps["task_factory"].create_task(compute_node, run)
        deps["task_repo"].save(compute_task)

        # Run the queued dispatch. Under edge-based dispatch there is no
        # execution-time AND-join: the explicitly enqueued COMPUTE runs
        # even though trigger-1 is not completed (the engine would never
        # have enqueued it without a taken edge in the first place).
        await engine.orchestrate_execute_node(run.id, compute_task.id)

        saved_run = deps["run_repo"].get_by_id(run.id)

        projected = deps["event_publisher"].projected_variables()
        assert "compute-1" in projected
        assert "compute-1" in saved_run.context.completed_nodes

        # The task completed normally — no execution-time deferral.
        saved_task = deps["task_repo"].get_by_id(compute_task.id)
        assert saved_task.status == TaskStatus.COMPLETED

    async def test_predecessor_guard_exempts_loop_body_tasks(self, engine_with_queue):
        """The Fix B guard must NOT defer loop body tasks (tasks with
        ``loop_context`` set), because the LOOP node that is their DAG
        predecessor is intentionally kept in a non-completed state while
        iterations are running. Deferring body tasks would stall every loop.
        """
        deps = engine_with_queue
        engine = deps["engine"]

        workflow = _build_trigger_compute_workflow()
        deps["workflow_repo"].save(workflow)

        topology = GraphTopology.from_workflow(workflow)
        # The predecessor (trigger-1) is intentionally NOT in completed_nodes.
        # Normally the guard would defer, but loop_context exempts body tasks.
        run = WorkflowRun(
            id="run-queued-loopbody-1",
            workflow_id=workflow.id,
            status=RunStatus.RUNNING,
            context=RunContext(variables={}, topology=topology),
        )
        deps["run_repo"].save(run)

        compute_node = workflow.get_node("compute-1")
        from app.layer1_domain.value_objects.loop_scope import LoopScope
        compute_task = deps["task_factory"].create_task(compute_node, run)
        compute_task.scope_stack = [
            LoopScope(loop_node_id="trigger-1", invocation_id="inv-1", iter=0, total=1),
        ]
        deps["task_repo"].save(compute_task)

        # Despite predecessor not being ready, scope_stack exempts loop body tasks.
        await engine.orchestrate_execute_node(run.id, compute_task.id)

        saved_run = deps["run_repo"].get_by_id(run.id)
        # Handler ran because loop_context exempted the task from the guard.
        # Phase 3b.1: verify via the emitted NodeOutputProduced write-set
        # instead of the aggregate — the engine no longer mutates
        # ``run.context.variables``.
        projected = deps["event_publisher"].projected_variables()
        assert "compute-1" in projected
        assert is_file_ref(projected["compute-1"])
