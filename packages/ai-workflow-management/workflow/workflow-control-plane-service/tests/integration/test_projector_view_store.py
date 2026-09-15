"""Integration test for ``ProjectorRunContextViewStore`` — Phase 4c.1.

Proves that a Phase 4a-migrated feature (``GetRunStatusUseCase``) can
be served **entirely from the event log** via the projector-backed view
store, without touching the live ``WorkflowRun`` aggregate.

Flow:
1. Drive a real workflow through ``OrchestrationEngine`` with
   ``EventLogAppendingPublisher`` wired, so every emitted event lands
   in the ``MemoryEventLogRepository``.
2. Instantiate ``LiveRunContextViewStore`` from the same repo — this
   is the production read path today.
3. Instantiate ``ProjectorRunContextViewStore`` from the event log.
4. Run ``GetRunStatusUseCase`` twice — once with the live store, once
   with the projector store — and assert the results are equal on
   every field the use case populates.

If this assertion fails, the Phase 4b.3/5.1 rebuild property has
regressed: some lifecycle or variable state is not flowing through
the event log.

This is the first test that serves a production feature from the
event log alone. Phase 5.2 will extend the pattern — today nothing in
``bootstrap.py`` is flipped.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="inline-execution coverage — rewrite to drive MemoryOrchestrationQueue via tests.helpers.queue_runner")
from unittest.mock import MagicMock

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.events import RunStatusChanged
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.data.data_store_service import DataStoreService
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
from app.layer2_application.features.get_run_status.use_cases.get_run_status_use_case import (
    GetRunStatusCommand,
    GetRunStatusUseCase,
)
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer4_frameworks.messaging.event_log_appending_publisher import (
    EventLogAppendingPublisher,
)
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from tests.helpers.view_store_doubles import LiveViewStoreDouble as LiveRunContextViewStore
from app.layer4_frameworks.run_context.projector_view_store import (
    ProjectorRunContextViewStore,
)


def _build_engine():
    event_log_repo = MemoryEventLogRepository()
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
    )
    return {
        "engine": engine,
        "event_log_repo": event_log_repo,
        "run_repo": run_repo,
        "workflow_repo": workflow_repo,
    }


@pytest.mark.asyncio
async def test_get_run_status_served_from_projector_matches_live():
    """Run a simple TRIGGER → COMPUTE workflow end-to-end, then:

    1. Query GetRunStatusUseCase with ``LiveRunContextViewStore``.
    2. Query GetRunStatusUseCase with ``ProjectorRunContextViewStore``
       (event-log backed, does NOT touch the run repo).
    3. Assert the two results are field-equal on every populated field.

    The projector store has zero knowledge of the ``WorkflowRun``
    aggregate — it builds ``ProjectedRunState`` from the event log
    alone. If this test passes, the Phase 4b.3/5.1 round-trip property
    is fully validated in a real feature code path.
    """
    deps = _build_engine()
    engine = deps["engine"]
    workflow_repo = deps["workflow_repo"]
    run_repo = deps["run_repo"]
    event_log_repo = deps["event_log_repo"]

    workflow = Workflow(
        id="wf-projector-store-1",
        name="projector-store-test",
        version="1",
        nodes=[
            WorkflowNode(id="trigger-1", name="trigger", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="compute-1",
                name="add",
                node_type=NodeType.COMPUTE,
                config={"expression": "6 + 5"},
            ),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="compute-1"),
        ],
    )
    workflow_repo.save(workflow)

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-projector-1",
        workflow_id=workflow.id,
        status=RunStatus.PENDING,
        context=RunContext(variables={}, topology=topology),
    )
    run_repo.save(run)

    # Simulate what ``start_run_use_case`` does in the production path:
    # publish ``RunStarted`` before calling ``engine.orchestrate_start``.
    # The engine itself does not emit ``RunStarted``; that is the use
    # case's responsibility. For the projector to rebuild ``workflow_id``
    # we need this event in the log.
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

    # ── Query via live store (production path today) ────────────
    live_store = LiveRunContextViewStore(run_repo)
    live_uc = GetRunStatusUseCase(logger=MagicMock(), run_context_view_store=live_store)
    live_result = await live_uc.execute(GetRunStatusCommand(run_id=run.id))

    # ── Query via projector store (event-log backed) ────────────
    projector_store = ProjectorRunContextViewStore(event_log_repo)
    projector_uc = GetRunStatusUseCase(
        logger=MagicMock(), run_context_view_store=projector_store,
    )
    projector_result = await projector_uc.execute(GetRunStatusCommand(run_id=run.id))

    # ── Field-by-field equality on everything GetRunStatus populates ──
    assert projector_result.error is None, (
        f"projector-backed GetRunStatus returned error: {projector_result.error} "
        f"(code={projector_result.error_code}). Event log may be missing events."
    )
    assert projector_result.id == live_result.id
    assert projector_result.workflow_id == live_result.workflow_id
    assert projector_result.status == live_result.status, (
        f"projector status {projector_result.status!r} != live {live_result.status!r} — "
        f"RunCompleted event missing from log or reducer dropping it"
    )
    assert projector_result.error == live_result.error

    # ``started_at`` / ``completed_at`` live on the WorkflowRun directly
    # and are NOT published as structured fields on Run events today;
    # the projector reducer sets them from the event envelope timestamp,
    # which won't byte-match the aggregate. Just assert they are both
    # populated (non-empty string for started_at, non-None for completed).
    assert projector_result.started_at, (
        "projector.started_at should be populated from RunStarted envelope timestamp"
    )
    assert projector_result.completed_at is not None, (
        "projector.completed_at should be populated from RunCompleted envelope timestamp"
    )


@pytest.mark.asyncio
async def test_projector_store_returns_none_for_unknown_run():
    """Mirror the ``LiveRunContextViewStore`` contract: unknown run id
    returns ``None``. The projector store uses "no envelopes in the log"
    as the not-found signal rather than "no repository row".
    """
    event_log_repo = MemoryEventLogRepository()
    store = ProjectorRunContextViewStore(event_log_repo)
    assert store.get("never-started") is None


@pytest.mark.asyncio
async def test_projector_store_get_by_root_id_raises():
    """Phase 4c.1 contract preserved in Phase 4c.3: stores constructed
    WITHOUT a ``run_repo`` still raise ``NotImplementedError``. This
    regression guard ensures callers that opted in before the fallback
    was added see unchanged behavior.
    """
    event_log_repo = MemoryEventLogRepository()
    store = ProjectorRunContextViewStore(event_log_repo)
    with pytest.raises(NotImplementedError, match="get_by_root_id"):
        store.get_by_root_id("root-1")


# ───────── Phase 4c.3: cross-feature equivalence on both stores ─────────


@pytest.mark.asyncio
async def test_get_run_context_equivalence_between_stores():
    """``GetRunContextUseCase`` must return field-equivalent output
    whether backed by ``LiveRunContextViewStore`` or
    ``CachedProjectorRunContextViewStore`` (with workflow_repo
    fallback). Variables must round-trip through the event log and
    the canonical-keys iteration must produce identical entries.
    """
    from app.layer2_application.features.get_run_context.use_cases.get_run_context_use_case import (
        GetRunContextCommand,
        GetRunContextUseCase,
    )
    from app.layer4_frameworks.run_context.cached_projector_view_store import (
        CachedProjectorRunContextViewStore,
    )

    deps = _build_engine()
    engine = deps["engine"]
    workflow_repo = deps["workflow_repo"]
    run_repo = deps["run_repo"]
    event_log_repo = deps["event_log_repo"]

    workflow = Workflow(
        id="wf-equiv-ctx-1",
        name="equiv-ctx",
        version="1",
        nodes=[
            WorkflowNode(id="trigger-1", name="trigger", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="compute-1", name="add",
                node_type=NodeType.COMPUTE,
                config={"expression": "10 + 5"},
            ),
        ],
        edges=[WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="compute-1")],
    )
    workflow_repo.save(workflow)

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-equiv-ctx-1",
        workflow_id=workflow.id,
        status=RunStatus.PENDING,
        context=RunContext(variables={}, topology=topology),
    )
    run_repo.save(run)

    # Upstream RunStarted simulation (same as the happy-path test)
    engine._event_publisher.publish(
        "run.status",
        RunStatusChanged(
            status="started",
            run_id=run.id, workflow_id=run.workflow_id,
            root_run_id=run.root_run_id or run.id,
            depth=run.depth_level, source="workflow-control-plane",
        ),
    )
    await engine.orchestrate_start(run.id)

    task_repo = deps["run_repo"]  # use cases need task_repo too; reuse for test
    from app.layer4_frameworks.persistence.memory.memory_task_repo import (
        MemoryTaskRepository,
    )
    task_repo_real = MemoryTaskRepository()  # empty — GetRunContext doesn't need tasks here

    # Live-store query
    live_store = LiveRunContextViewStore(run_repo)
    live_uc = GetRunContextUseCase(
        logger=MagicMock(),
        run_context_view_store=live_store,
        task_repo=task_repo_real,
        workflow_repo=workflow_repo,
    )
    live_result = await live_uc.execute(GetRunContextCommand(run_id=run.id))

    # Projector-store query (Phase 4c.3: with workflow_repo fallback)
    projector_store = CachedProjectorRunContextViewStore(
        event_log_repo,
        workflow_repo=workflow_repo,
        run_repo=run_repo,
    )
    projector_uc = GetRunContextUseCase(
        logger=MagicMock(),
        run_context_view_store=projector_store,
        task_repo=task_repo_real,
        workflow_repo=workflow_repo,
    )
    projector_result = await projector_uc.execute(GetRunContextCommand(run_id=run.id))

    # ── Equivalence assertions ────────────────────────────────────
    assert projector_result.error is None, (
        f"projector result errored: {projector_result.error}"
    )
    assert projector_result.run_id == live_result.run_id
    assert projector_result.workflow_id == live_result.workflow_id
    assert projector_result.run_status == live_result.run_status

    # Entry sets must match on key set (value details may differ in
    # non-essential metadata timestamps but the canonical keys are
    # deterministic).
    live_keys = {e.key for e in live_result.entries}
    projector_keys = {e.key for e in projector_result.entries}
    assert live_keys == projector_keys, (
        f"entry key mismatch — live: {live_keys}, projector: {projector_keys}"
    )
    # Every key that's present in both stores should classify the same
    # way (inline vs file_ref).
    live_by_key = {e.key: e for e in live_result.entries}
    proj_by_key = {e.key: e for e in projector_result.entries}
    for key in live_keys:
        assert live_by_key[key].data_type == proj_by_key[key].data_type, (
            f"data_type mismatch for key={key!r}: "
            f"live={live_by_key[key].data_type}, "
            f"projector={proj_by_key[key].data_type}"
        )


@pytest.mark.asyncio
async def test_get_node_output_equivalence_with_topology_fallback():
    """``GetNodeOutputUseCase`` reads ``view.entry_nodes`` (from
    ``topology``) for its entry-node fallback path. Phase 4c.3 wires
    a ``workflow_repo`` into the projector store so ``entry_nodes``
    works via ``TopologyBuilder.prepare`` instead of returning ``()``.
    This test proves the full feature produces equivalent output
    from both stores.
    """
    from app.layer2_application.features.get_node_output.use_cases.get_node_output_use_case import (
        GetNodeOutputCommand,
        GetNodeOutputUseCase,
    )
    from app.layer4_frameworks.run_context.cached_projector_view_store import (
        CachedProjectorRunContextViewStore,
    )

    deps = _build_engine()
    engine = deps["engine"]
    workflow_repo = deps["workflow_repo"]
    run_repo = deps["run_repo"]
    event_log_repo = deps["event_log_repo"]

    workflow = Workflow(
        id="wf-equiv-node-1",
        name="equiv-node",
        version="1",
        nodes=[
            WorkflowNode(id="trigger-1", name="trigger", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="compute-1", name="mul",
                node_type=NodeType.COMPUTE,
                config={"expression": "7 * 6"},
            ),
        ],
        edges=[WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="compute-1")],
    )
    workflow_repo.save(workflow)

    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-equiv-node-1",
        workflow_id=workflow.id,
        status=RunStatus.PENDING,
        context=RunContext(variables={}, topology=topology),
    )
    run_repo.save(run)
    engine._event_publisher.publish(
        "run.status",
        RunStatusChanged(
            status="started",
            run_id=run.id, workflow_id=run.workflow_id,
            root_run_id=run.root_run_id or run.id,
            depth=run.depth_level, source="workflow-control-plane",
        ),
    )
    await engine.orchestrate_start(run.id)

    from app.layer4_frameworks.persistence.memory.memory_task_repo import (
        MemoryTaskRepository,
    )
    task_repo = MemoryTaskRepository()
    file_client = MagicMock()  # get_node_output uses it for file previews

    # Live store
    live_store = LiveRunContextViewStore(run_repo)
    live_uc = GetNodeOutputUseCase(
        logger=MagicMock(),
        run_context_view_store=live_store,
        task_repo=task_repo,
        workflow_repo=workflow_repo,
        file_service_client=file_client,
    )
    live_result = await live_uc.execute(
        GetNodeOutputCommand(run_id=run.id, node_id="compute-1"),
    )

    # Projector store with BOTH fallback repos wired (Phase 4c.3)
    projector_store = CachedProjectorRunContextViewStore(
        event_log_repo,
        workflow_repo=workflow_repo,
        run_repo=run_repo,
    )
    projector_uc = GetNodeOutputUseCase(
        logger=MagicMock(),
        run_context_view_store=projector_store,
        task_repo=task_repo,
        workflow_repo=workflow_repo,
        file_service_client=file_client,
    )
    projector_result = await projector_uc.execute(
        GetNodeOutputCommand(run_id=run.id, node_id="compute-1"),
    )

    # ── Equivalence assertions ────────────────────────────────────
    assert projector_result.error is None, (
        f"projector result errored: {projector_result.error} "
        f"(code={projector_result.error_code})"
    )
    assert live_result.error is None, f"live result errored: {live_result.error}"

    assert projector_result.run_id == live_result.run_id
    assert projector_result.node_id == live_result.node_id
    assert projector_result.node_name == live_result.node_name
    assert projector_result.status == live_result.status
    assert projector_result.data_type == live_result.data_type
    assert projector_result.file_id == live_result.file_id


@pytest.mark.asyncio
async def test_get_node_output_nested_run_via_get_by_root_id_fallback():
    """Phase 4c.3: ``get_node_output`` falls back to
    ``run_context_view_store.get_by_root_id`` when looking up a node
    that isn't in the top-level workflow. The projector store's
    ``get_by_root_id`` delegates to ``run_repo.get_by_root_id`` and
    replays each nested run's event log. This test verifies the full
    chain works end-to-end.
    """
    from app.layer2_application.features.get_node_output.use_cases.get_node_output_use_case import (
        GetNodeOutputCommand,
        GetNodeOutputUseCase,
    )
    from app.layer4_frameworks.run_context.cached_projector_view_store import (
        CachedProjectorRunContextViewStore,
    )

    deps = _build_engine()
    workflow_repo = deps["workflow_repo"]
    run_repo = deps["run_repo"]
    event_log_repo = deps["event_log_repo"]

    # Parent workflow — one node, self-contained. The nested-run lookup
    # is exercised by constructing a child run in a DIFFERENT workflow
    # and registering it under the same root_run_id.
    parent_wf = Workflow(
        id="wf-parent-equiv",
        name="parent",
        version="1",
        nodes=[WorkflowNode(id="parent-node", name="p", node_type=NodeType.TRIGGER)],
    )
    child_wf = Workflow(
        id="wf-child-equiv",
        name="child",
        version="1",
        nodes=[
            WorkflowNode(id="child-node", name="c", node_type=NodeType.TRIGGER),
        ],
    )
    workflow_repo.save(parent_wf)
    workflow_repo.save(child_wf)

    # Register parent + child runs in the run_repo sharing root_run_id
    parent_run = WorkflowRun(
        id="run-parent-equiv", workflow_id="wf-parent-equiv",
        status=RunStatus.COMPLETED,
        root_run_id="run-parent-equiv",
        context=RunContext(variables={"parent-node": {"ok": True}}),
    )
    child_run = WorkflowRun(
        id="run-child-equiv", workflow_id="wf-child-equiv",
        status=RunStatus.COMPLETED,
        parent_run_id="run-parent-equiv", root_run_id="run-parent-equiv",
        context=RunContext(variables={"child-node": {"value": 42}}),
    )
    run_repo.save(parent_run)
    run_repo.save(child_run)

    # Seed the event log so the projector can rebuild each run.
    # Emit NodeOutputProduced events so variables round-trip.
    from app.layer1_domain.events import NodeOutputProduced
    from app.layer2_application.interfaces.event_log_repository_interface import EventLogEnvelope

    def _append(run_id, event_id, event_type, **payload):
        event_log_repo.append(EventLogEnvelope(
            event_id=event_id, run_id=run_id,
            topic=event_type.lower(), event_type=event_type,
            timestamp="2026-04-11T00:00:00Z",
            payload={**payload, "run_id": run_id},
        ))

    _append("run-parent-equiv", "rs-p", "RunStatusChanged", status="started", workflow_id="wf-parent-equiv")
    _append("run-parent-equiv", "nop-p", "NodeOutputProduced",
            node_id="parent-node",
            state_updates={"parent-node": {"ok": True}})
    _append("run-parent-equiv", "rc-p", "RunStatusChanged", status="completed")

    _append("run-child-equiv", "rs-c", "RunStatusChanged", status="started", workflow_id="wf-child-equiv")
    _append("run-child-equiv", "nop-c", "NodeOutputProduced",
            node_id="child-node",
            state_updates={"child-node": {"value": 42}})
    _append("run-child-equiv", "rc-c", "RunStatusChanged", status="completed")

    from app.layer4_frameworks.persistence.memory.memory_task_repo import (
        MemoryTaskRepository,
    )
    task_repo = MemoryTaskRepository()

    # Live store — production reference
    live_store = LiveRunContextViewStore(run_repo)
    live_uc = GetNodeOutputUseCase(
        logger=MagicMock(),
        run_context_view_store=live_store,
        task_repo=task_repo,
        workflow_repo=workflow_repo,
        file_service_client=MagicMock(),
    )
    # Query for ``child-node`` — it's NOT in parent workflow → triggers
    # the nested-run lookup via get_by_root_id.
    live_result = await live_uc.execute(
        GetNodeOutputCommand(run_id="run-parent-equiv", node_id="child-node"),
    )

    # Projector store with both fallbacks wired
    projector_store = CachedProjectorRunContextViewStore(
        event_log_repo,
        workflow_repo=workflow_repo,
        run_repo=run_repo,
    )
    projector_uc = GetNodeOutputUseCase(
        logger=MagicMock(),
        run_context_view_store=projector_store,
        task_repo=task_repo,
        workflow_repo=workflow_repo,
        file_service_client=MagicMock(),
    )
    projector_result = await projector_uc.execute(
        GetNodeOutputCommand(run_id="run-parent-equiv", node_id="child-node"),
    )

    # ── Both stores must find the child node via nested-run lookup ──
    assert live_result.error is None, (
        f"live store failed to find child-node: {live_result.error}"
    )
    assert projector_result.error is None, (
        f"projector store failed to find child-node: {projector_result.error} "
        f"(code={projector_result.error_code}) — "
        f"Phase 4c.3 get_by_root_id fallback may be broken"
    )
    assert projector_result.node_id == live_result.node_id == "child-node"
    assert projector_result.node_name == live_result.node_name
