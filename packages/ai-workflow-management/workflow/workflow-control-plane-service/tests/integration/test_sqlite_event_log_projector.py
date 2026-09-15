"""End-to-end test: engine → SQLite event log → projector rebuild.

Phase 4b.6 proves the SQLite backend is a drop-in replacement for
``MemoryEventLogRepository`` on the full publish path. This test
drives a real ``OrchestrationEngine`` with an
``EventLogAppendingPublisher`` wrapping ``SqliteEventLogRepository``,
then rebuilds the run state through the Phase 4b.3 reducer and
asserts the round-trip matches the live aggregate.

This is the prerequisite for production-wiring the projector-backed
view store from Phase 4c.1: if the SQLite backend can serve the full
engine → log → rebuild loop, it's ready to replace
``MemoryEventLogRepository`` in ``bootstrap.py``.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
from unittest.mock import MagicMock

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.events import RunStatusChanged
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.orchestration.loop_driver.event_driven_loop_driver import EventDrivenLoopDriver
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.projection import rebuild_run_state
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
from app.layer2_application.interfaces.orchestration_queue_interface import (
    ExecuteNodeAction,
    StartRunAction,
)
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer4_frameworks.messaging.event_log_appending_publisher import (
    EventLogAppendingPublisher,
)
from app.layer2_application.orchestration_queue.memory_queue import MemoryOrchestrationQueue
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from app.layer4_frameworks.persistence.sqlite.sqlite_event_log_repo import (
    SqliteEventLogRepository,
)
from app.layer4_frameworks.run_context.projector_view_store import (
    ProjectorRunContextViewStore,
)
from app.layer2_application.features.get_run_status.use_cases.get_run_status_use_case import (
    GetRunStatusCommand,
    GetRunStatusUseCase,
)


async def _drain_queue(engine: OrchestrationEngine, queue: MemoryOrchestrationQueue, max_steps: int = 100) -> int:
    """Drain the queue by dispatching actions to the engine."""
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
    return steps


def _build_engine(event_log_repo):
    """Build a fully wired engine with the supplied event log backend."""
    inner_publisher = MagicMock()
    event_publisher = EventLogAppendingPublisher(
        inner=inner_publisher,
        event_log_repo=event_log_repo,
    )

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

    handlers = NodeHandlerRegistry()
    handlers.register(NodeType.TRIGGER, TriggerNodeHandler())
    handlers.register(NodeType.COMPUTE, ComputeNodeHandler(resolver))

    task_factory = TaskFactory()
    successor_processor = SuccessorProcessor(
        handlers, task_factory, data_store_service=data_store,
    )

    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    workflow_repo = MemoryWorkflowRepository()

    stage_recorder = StageRecorder(MagicMock())
    stage_recorder.record = MagicMock()

    driver = EventDrivenLoopDriver(resolver)

    engine = OrchestrationEngine(
        handler_registry=handlers,
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
        event_driven_loop_driver=driver,
    )

    # Set up the queue for event-driven execution
    queue = MemoryOrchestrationQueue()
    engine.set_queue(queue)

    return {
        "engine": engine,
        "queue": queue,
        "event_log_repo": event_log_repo,
        "run_repo": run_repo,
        "workflow_repo": workflow_repo,
    }


@pytest.mark.asyncio
async def test_engine_sqlite_projector_rebuild_round_trip():
    """Drive a TRIGGER → COMPUTE workflow end-to-end with a SQLite-backed
    event log, rebuild the state, assert equivalence with live state.

    This is the same property the Phase 4b.3 rebuild test asserts
    against the in-memory backend. Phase 4b.6 proves the SQLite backend
    has the same contract.
    """
    event_log_repo = SqliteEventLogRepository(":memory:")
    try:
        deps = _build_engine(event_log_repo)
        engine = deps["engine"]
        queue = deps["queue"]
        workflow_repo = deps["workflow_repo"]
        run_repo = deps["run_repo"]

        workflow = Workflow(
            id="wf-sqlite-rebuild-1",
            name="sqlite-rebuild",
            version="1",
            nodes=[
                WorkflowNode(id="trigger-1", name="trigger", node_type=NodeType.TRIGGER, config={}),
                WorkflowNode(
                    id="compute-1",
                    name="double",
                    node_type=NodeType.COMPUTE,
                    config={"expression": "21 * 2"},
                ),
            ],
            edges=[
                WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="compute-1"),
            ],
        )
        workflow_repo.save(workflow)

        topology = GraphTopology.from_workflow(workflow)
        run = WorkflowRun(
            id="run-sqlite-1",
            workflow_id=workflow.id,
            status=RunStatus.PENDING,
            context=RunContext(variables={}, topology=topology),
        )
        run_repo.save(run)

        # Simulate start_run_use_case publishing RunStarted (the engine
        # itself does not emit this; the projector needs it to populate
        # workflow_id). Same setup as test_projector_view_store.py.
        engine._event_publisher.publish(
            "run.status",
            RunStatusChanged(
                status="started",
                run_id=run.id,
                workflow_id=run.workflow_id,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
            ),
        )

        await engine.orchestrate_start(run.id)
        # Drain the queue to process all enqueued actions
        await _drain_queue(engine, queue)

        # ── Live state ──────────────────────────────────────────────
        live_run = run_repo.get_by_id(run.id)
        assert live_run.status == RunStatus.COMPLETED
        assert "trigger-1" in live_run.context.completed_nodes
        assert "compute-1" in live_run.context.completed_nodes

        # ── Projector rebuild from SQLite log ───────────────────────
        projected = rebuild_run_state(run.id, event_log_repo)

        assert projected.workflow_id == live_run.workflow_id
        assert projected.status == "COMPLETED"
        assert projected.completed_nodes == frozenset(live_run.context.completed_nodes)
        assert projected.skipped_nodes == frozenset(live_run.context.skipped_nodes)

        # Variables round-trip — exact equality on every live key
        for key, live_value in live_run.context.variables.items():
            assert key in projected.variables
            assert projected.variables[key] == live_value

        # ── Feature code path via projector view store ──────────────
        # This proves the full stack: SQLite log → projector store →
        # GetRunStatusUseCase. Same assertion as Phase 4c.1's
        # test_get_run_status_served_from_projector_matches_live but
        # with the SQLite backend instead of in-memory.
        projector_store = ProjectorRunContextViewStore(event_log_repo)
        uc = GetRunStatusUseCase(
            logger=MagicMock(), run_context_view_store=projector_store,
        )
        result = await uc.execute(GetRunStatusCommand(run_id=run.id))

        assert result.error is None
        assert result.id == run.id
        assert result.workflow_id == workflow.id
        assert result.status == "COMPLETED"
    finally:
        event_log_repo.close()


@pytest.mark.asyncio
async def test_sqlite_log_survives_across_engine_sessions():
    """Write events through one engine session, close the SQLite
    connection, reopen the same DB file in a new session, and rebuild.
    This is the durability property that justifies the SQLite backend
    over the memory backend: a crashed worker can rebuild state from
    the persisted log.
    """
    fd, db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        # ── First session — write events via the engine ─────────────
        writer_log = SqliteEventLogRepository(db_path)
        try:
            deps = _build_engine(writer_log)
            engine = deps["engine"]
            queue = deps["queue"]
            workflow_repo = deps["workflow_repo"]
            run_repo = deps["run_repo"]

            workflow = Workflow(
                id="wf-sqlite-durability",
                name="sqlite-durability",
                version="1",
                nodes=[
                    WorkflowNode(id="trigger-1", name="trigger", node_type=NodeType.TRIGGER, config={}),
                    WorkflowNode(
                        id="compute-1",
                        name="sum",
                        node_type=NodeType.COMPUTE,
                        config={"expression": "3 + 4"},
                    ),
                ],
                edges=[
                    WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="compute-1"),
                ],
            )
            workflow_repo.save(workflow)
            topology = GraphTopology.from_workflow(workflow)
            run = WorkflowRun(
                id="run-durability-1",
                workflow_id=workflow.id,
                status=RunStatus.PENDING,
                context=RunContext(variables={}, topology=topology),
            )
            run_repo.save(run)
            engine._event_publisher.publish(
                "run.status",
                RunStatusChanged(
                    status="started",
                    run_id=run.id,
                    workflow_id=run.workflow_id,
                    root_run_id=run.id,
                    depth=0,
                    source="workflow-control-plane",
                ),
            )
            await engine.orchestrate_start(run.id)
            # Drain the queue to process all enqueued actions
            await _drain_queue(engine, queue)
        finally:
            writer_log.close()

        # ── Second session — new reader, rebuild from persisted file ──
        reader_log = SqliteEventLogRepository(db_path)
        try:
            projected = rebuild_run_state("run-durability-1", reader_log)
            # Even without the live ``WorkflowRun`` aggregate (the first
            # session's run_repo is gone), the projector rebuilds the
            # lifecycle + variables state from the log alone.
            assert projected.workflow_id == "wf-sqlite-durability"
            assert projected.status == "COMPLETED"
            assert "compute-1" in projected.completed_nodes
            assert "compute-1" in projected.variables, (
                "Phase 5.1 variables round-trip must survive a DB reopen"
            )
        finally:
            reader_log.close()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
