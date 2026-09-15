"""Orchestration engine: shared logic for running workflows.

Extracted from the old monolithic orchestration use case into a reusable
domain service that multiple use cases can depend on.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Any
from collections.abc import AsyncIterator, Mapping

if TYPE_CHECKING:
    from app.layer2_application.repositories.task_repository_interface import ITaskRepository
    from app.layer2_application.repositories.run_repository_interface import IRunRepository
    from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
    from app.layer2_application.interfaces.event_publisher_interface import IEventPublisher
    from app.layer2_application.interfaces.callback_publisher_interface import ICallbackPublisher
    from app.layer2_application.interfaces.file_service_client_interface import IFileServiceClient
    from app.layer2_application.interfaces.monitor_interface import IMonitor
    from app.layer2_application.interfaces.run_context_view_interface import IRunContextView
    from app.layer2_application.interfaces.snapshot_repository_interface import ISnapshotRepository
    from app.layer2_application.resolve.variable_resolver import VariableResolver
    from app.layer2_application.data.data_store_service import DataStoreService
    from app.layer2_application.data.csv_converter import CsvConverter
    from app.layer2_application.orchestration.loop_driver.event_driven_loop_driver import (
        EventDrivenLoopDriver,
    )

from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.instance_selection import (
    resolve_target_instance_id,
    resolve_target_host,
    SELECTION_METADATA_KEY,
)


def _pin_selection_for(run: Any):
    """SA-2437: the worker-instance pin selection scoped to its OWN flow.

    Returns the selection only when it belongs to THIS run's workflow_id — so a
    flow's pin is never applied to a sub-flow it calls. A child sub-flow run has
    a different workflow_id (and, today, empty metadata anyway), so a config
    tagged with the parent flow's id is ignored for it. Belt-and-suspenders:
    even if run metadata were ever inherited by children, the id gate holds.
    """
    sel = (getattr(run, "metadata", None) or {}).get(SELECTION_METADATA_KEY)
    if not isinstance(sel, dict):
        return None
    wf = sel.get("workflow_id")
    if wf and wf != getattr(run, "workflow_id", None):
        return None
    return sel
from app.layer1_domain.events import (
    EventTopic,
    RunStatusChanged,
    NodeDispatched,
    NodeCompleted,
    NodeFailed,
    NodeCancelled,
    ContextUpdated,
    InputSubmitted,
    ArtifactAdded,
    NodeInputEdited,
    ChildRunStarted,
    ChildRunCompleted,
    ChildRunFailed,
    ChildRunCancelled,
)
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.exceptions import CrossWorkflowCycleError, WorkflowExecutionError, ExpressionError, InputRequirementError, LoopCollectionTypeError
from app.layer2_application.node_handlers.base import NodeHandlerRegistry, NodeHandlerResult, invoke_handler
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.orchestration.successor_processor import (
    SuccessorProcessor,
    BUILTIN_NODE_TYPES,
    PENDING_NODE_TYPES,
    BRANCHING_NODE_TYPES,
    ProcessingResult,
    discard_stale_node_variable,
)
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.resolve.variable_resolver import set_current_run_id, reset_current_run_id
from app.layer2_application.definition.workflow_snapshot_loader import (
    capture_snapshot,
    load_workflow_for_run as _load_workflow_for_run_helper,
)
from app.layer2_application.orchestration.run_scope_manager import RunScopeManager
from app.layer2_application.events.event_emitter import EventEmitter
from app.layer2_application.orchestration.child_workflow_coordinator import (
    ChildWorkflowCoordinator,
)
from app.layer2_application.orchestration.node_execution_runner import (
    ExecutionContext,
    NodeExecutionRunner,
)
from app.layer2_application.orchestration.loop_iteration_driver import LoopIterationDriver
from app.layer1_domain.value_objects.resolution_scope import (
    IterationLocalsFrame,
    ResolutionScope,
    ResolutionScopeBuilder,
    get_current_scope,
    reset_current_scope,
    set_current_scope,
)
from app.layer1_domain.fanout import FanoutDecision
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer1_domain.entities.data_reference import (
    is_file_ref,
    should_keep_inline,
)
from app.layer1_domain.events import (
    NodeOutputProduced,
    NodeInput,
)
from app.layer1_domain.value_objects.event_id import EventId
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer2_application.interfaces.orchestration_queue_interface import ExecuteNodeAction
from app.layer2_application.run_save_retry import save_run_with_cas_retry
from app.layer2_application.orchestration_queue.memory_queue import MemoryOrchestrationQueue

# Run ids whose per-run lock is held by the current asyncio task (via
# ``OrchestrationEngine.locked_run``). Lets nested run-scoped work — e.g.
# a child workflow completing synchronously inside the parent's locked
# section — re-enter the parent's critical section instead of
# deadlocking on the non-reentrant ``asyncio.Lock``.
_held_run_locks: ContextVar[frozenset[str]] = ContextVar(
    "orchestration_held_run_locks", default=frozenset(),
)

# Task statuses for which a freshly-arriving ExecuteNodeAction is ALWAYS a
# redelivery (at-least-once queue, no dedup key) and must be dropped so a
# non-idempotent handler (DATA_EDIT re-provisioning / replaying data-factory
# row ops) is not re-run. Deliberately EXCLUDES:
#   * READY / WAITING — the only pre-execution statuses; a first dispatch runs.
#   * RUNNING / RETRYING — RerunTaskUseCase resets a task to RUNNING and
#     re-executes the SAME task id via orchestrate_execute_node, so RUNNING is a
#     legitimate (re-)execution target and cannot be treated as a duplicate here.
# PENDING (paused on user input — resumes via ResumeTaskAction, never a fresh
# ExecuteNodeAction) and the terminal statuses are never legitimate targets.
_NON_DISPATCHABLE_STATUSES = frozenset({
    TaskStatus.PENDING, TaskStatus.COMPLETED,
    TaskStatus.FAILED, TaskStatus.CANCELLED,
})


class OrchestrationEngine(IOrchestrationEngine):
    """Shared orchestration logic for dispatching tasks and checking run completion."""

    def __init__(
        self,
        handler_registry: NodeHandlerRegistry,
        task_factory: TaskFactory,
        successor_processor: SuccessorProcessor,
        stage_recorder: StageRecorder,
        task_repo: ITaskRepository,
        run_repo: IRunRepository,
        event_publisher: IEventPublisher,
        callback_publisher: ICallbackPublisher | None = None,
        file_service_client: IFileServiceClient | None = None,
        csv_converter: CsvConverter | None = None,
        workflow_repo: IWorkflowRepository | None = None,
        workflow_snapshot_repo: ISnapshotRepository | None = None,
        variable_resolver: VariableResolver | None = None,
        data_store_service: DataStoreService | None = None,
        monitor: IMonitor | None = None,
        event_driven_loop_driver: EventDrivenLoopDriver | None = None,
        max_concurrent_iterations_per_run: int = 100,
        run_context_view_store: IRunContextView | None = None,
        scope_builder: ResolutionScopeBuilder | None = None,
        executor_mode: str = "in_process",
        node_output_repo: Any = None,
        environment_repo: Any = None,
        workflow_environment_repo: Any = None,
        workflow_section_repo: Any = None,
        section_membership_repo: Any = None,
    ) -> None:
        self._handler_registry = handler_registry
        self._task_factory = task_factory
        self._successor_processor = successor_processor
        self._stage_recorder = stage_recorder
        self._task_repo = task_repo
        self._run_repo = run_repo
        self._event_publisher = event_publisher
        self._callback_publisher = callback_publisher
        self._file_service_client = file_service_client
        self._csv_converter = csv_converter
        self._workflow_repo = workflow_repo
        self._workflow_snapshot_repo = workflow_snapshot_repo
        self._variable_resolver = variable_resolver
        self._data_store_service = data_store_service
        self._monitor = monitor
        self._event_driven_loop_driver = event_driven_loop_driver
        self._max_concurrent_iterations_per_run = max_concurrent_iterations_per_run
        # AIW_RUN_NODE_OUTPUT + AIW_SCHEMA_DEF (migration 055). Optional: absent
        # in every test that predates it, and the mirror is a no-op then.
        #
        # It lives on the ENGINE, not only on CompleteTaskUseCase, because
        # ``apply_state_updates_as_files`` is the single place every node output
        # reaches — worker completions AND the builtin/inline handlers, which
        # never pass through CompleteTaskUseCase at all. Measured on tenant-1
        # 2026-08-08: 152 of 420 completed tasks were builtins with a real
        # file_ref output and ZERO rows, and 51 of 88 schema hashes on the wire
        # had no AIW_SCHEMA_DEF entry, purely because the only writer sat on the
        # worker path. See spec section 17.8.
        self._node_output_repo = node_output_repo
        self._view_store = run_context_view_store
        self._scope_builder = scope_builder
        # Phase 3 Sub 3.5 / Gate 3 — when ``executor_mode == "executor"``
        # the engine STOPS invoking handlers in-process. Each of the six
        # handler-invocation sites short-circuits to publish ``NodeInput``
        # via :meth:`_emit_node_input_for_executor` and returns; the
        # standalone ``ExecutorConsumer`` pod picks up ``NodeInput`` from
        # the broker, invokes the handler, and emits the authoritative
        # ``NodeOutputProduced`` / ``NodeCompleted`` events. The engine
        # retains task creation + ``NodeDispatched`` emission only.
        # Default ``"in_process"`` keeps existing tests + behaviour;
        # ``"executor"`` is the only other value (see ``_is_executor_mode``).
        self._executor_mode = executor_mode
        # Default to an in-process MemoryOrchestrationQueue so the engine is
        # always usable out of the box; bootstrap (or tests) replace it via
        # set_queue() when a broker-backed or shared queue is required.

        self._queue: Any = MemoryOrchestrationQueue()
        # Per-run lock + scope + resolver-cache lifecycle lives in a
        # dedicated collaborator. See run_scope_manager.py — extracted
        # from the engine to keep this class focused on orchestration.
        self._scope = RunScopeManager(
            run_repo=run_repo,
            workflow_repo=workflow_repo,
            workflow_snapshot_repo=workflow_snapshot_repo,
            scope_builder=scope_builder,
            variable_resolver=variable_resolver,
        )
        # All publish/buffer/drain primitives live on the emitter; the
        # engine exposes them as ``publish_event`` / ``publish_events``
        # (direct) and ``emit`` / ``drain_events`` (buffered).
        self._emitter = EventEmitter(event_publisher)
        # WORKFLOW-node ↔ child-run lifecycle (spawn, propagate
        # completion, propagate failure) lives in a dedicated
        # coordinator. The engine retains thin delegators for
        # ``_start_child_workflow``, ``_complete_parent_tracking_task``,
        # and ``fail_parent_tracking_task`` because both internal
        # call sites and the external IOrchestrationEngine interface
        # bind to those names.
        # SA-2365: env repo lets the coordinator bind a child sub-workflow run
        # to a SPECIFIC environment (the WORKFLOW node's environment_id).
        # SA-2496: workflow_environment_repo guards a DYNAMIC-resolved env id
        # against the target subflow's SA-2450 assignment.
        self._environment_repo = environment_repo
        self._workflow_environment_repo = workflow_environment_repo
        self._workflow_section_repo = workflow_section_repo  # SA-2605
        self._section_membership_repo = section_membership_repo  # SA-2605
        self._child_coord = ChildWorkflowCoordinator(
            engine=self,
            run_repo=run_repo,
            task_repo=task_repo,
            workflow_repo=workflow_repo,
            workflow_snapshot_repo=workflow_snapshot_repo,
            data_store_service=data_store_service,
            variable_resolver=variable_resolver,
            environment_repo=environment_repo,
            workflow_environment_repo=workflow_environment_repo,
            workflow_section_repo=workflow_section_repo,
            section_membership_repo=section_membership_repo,
        )
        # Per-mode runners for ``_exec_uniform``'s completion_mode
        # switch. See ``node_execution_runner.py`` — the engine's
        # ``_exec_uniform`` now reduces to preamble + dispatch +
        # postamble (~50 lines instead of ~310).
        self._exec_runner = NodeExecutionRunner(engine=self)
        # LOOP node ↔ body-iteration lifecycle (body-complete hook +
        # iteration advancement). See ``loop_iteration_driver.py``.
        self._loop_driver = LoopIterationDriver(
            engine=self,
            run_repo=run_repo,
            task_repo=task_repo,
            handler_registry=handler_registry,
            task_factory=task_factory,
            monitor=monitor,
            event_driven_loop_driver=event_driven_loop_driver,
        )

    # ------------------------------------------------------------------
    # Workflow-snapshot read path
    # ------------------------------------------------------------------
    #
    # Every engine read of a workflow during run execution goes through
    # ``load_workflow_for_run``. Use cases that hold a ``WorkflowRun`` and
    # need its workflow definition should call this method too — it's the
    # one place that resolves the snapshot, so the engine and the use case
    # observe the same immutable definition.
    #
    # ``WorkflowSnapshotMissingError`` (hard-fail) is raised when the run
    # has no ``workflow_snapshot_id`` or the row is missing. No fallback
    # to the live ``workflow_repo`` — see workflow-snapshot-plan.md §D3.

    def load_workflow_for_run(self, run: WorkflowRun) -> Workflow:
        workflow = _load_workflow_for_run_helper(run, self._workflow_snapshot_repo)
        self._ensure_topology(run, workflow)
        self._ensure_routing_state(run)
        return workflow

    def _ensure_routing_state(self, run: WorkflowRun) -> None:
        """Refill ``last_output_ports`` / ``last_taken_edge_ports`` from the
        projector when the reloaded aggregate has none.

        The sibling of ``_ensure_topology``, and the same root cause. Both maps
        rode ``AIW_WORKFLOW_RUNS.CONTEXT``; step 5b dropped the column, and
        unlike ``loop_state`` and ``node_visits`` — which got read-mode gates
        and their own tables — nothing was left to rebuild these.

        They decide ONE thing, and it is not cosmetic. ``decide_successors``
        reads them to route a resumed PENDING node: ``last_output_ports`` gives
        the port filter, ``last_taken_edge_ports`` the exact edge set. Empty,
        both fall through to "traverse every outgoing edge" — so a rejected
        HUMAN_ACTION also fires its confirm branch. ``_context_to_dict`` already
        names this failure verbatim as the reason the fields were serialised:
        *"the worker's reload saw empty maps and traversed ALL edges — confirm
        AND reject both ran (masked by the aliasing memory repo in tests)."*

        The write path never stopped: ``_record_node_output`` mirrors both onto
        the aggregate, and the projector reduces the same two fields off
        ``NodeOutputProduced``. Only the reload lost them, which is why this
        needs no new event — just a read from the store that already has them.

        Fills only a map that is entirely EMPTY, mirroring ``_ensure_topology``.
        A non-empty map means this call frame already wrote one, and an in-frame
        write must beat a view that has not folded the event yet.
        """
        ctx = getattr(run, "context", None)
        # ``getattr``: contract tests probe this path on a bare ``__new__``
        # engine, same as ``_record_node_output`` does for the view store.
        view_store = getattr(self, "_view_store", None)
        if ctx is None or view_store is None:
            return
        if ctx.last_output_ports and ctx.last_taken_edge_ports:
            return
        try:
            view = view_store.get(run.id)
        except Exception:
            # Routing is about to happen either way. A view-store failure here
            # is not worth turning into a run failure, but it MUST be visible —
            # degrading silently is what made the original bug so expensive.
            logging.getLogger(__name__).warning(
                "routing-state refill failed for run %s — successor routing "
                "may traverse every edge", run.id, exc_info=True,
            )
            return
        if view is None:
            return
        if not ctx.last_output_ports:
            ctx.last_output_ports.update(
                {str(k): str(v) for k, v in (getattr(view, "last_output_ports", None) or {}).items()}
            )
        if not ctx.last_taken_edge_ports:
            ctx.last_taken_edge_ports.update(
                {
                    str(k): dict(v)
                    for k, v in (getattr(view, "last_taken_edge_ports", None) or {}).items()
                    if isinstance(v, dict)
                }
            )

    @staticmethod
    def _ensure_topology(run: WorkflowRun, workflow: Workflow | None) -> None:
        """Rebuild ``run.context.topology`` when the reloaded aggregate has none.

        ``topology`` used to ride ``AIW_WORKFLOW_RUNS.CONTEXT``. Dropping that
        column (step 5b) left it EMPTY on every reload, and the audit that
        cleared the drop got this wrong: it recorded ``topology`` as *"safe —
        rebuilt from AIW_WORKFLOWS"*, which is true of the projector VIEW and
        false of the aggregate. Nothing rebuilt it here.

        The use cases all build it with ``GraphTopology.from_workflow`` and set it
        in memory, but the orchestration worker reloads the run through
        ``aget_by_id`` and then reads ``run.context.topology`` in five places
        (``:1873``, ``:1933``, ``:1987``, ``:2143``, ``:2214``). Empty topology
        means no successors, so a run completed having executed **nothing** — no
        error, no failed task, just two ``RunStatusChanged`` events.

        This is the chokepoint because it is the one place that holds both the
        run and its pinned workflow. Rebuilding is safe and deterministic:
        ``from_workflow`` is a pure function of the WORKFLOW SNAPSHOT the run is
        pinned to, which is exactly what the dropped column had serialised — the
        projector's own store makes the same claim of byte-identical output.

        Only fills an EMPTY topology, so a caller that already set one (every use
        case does, before the first save) keeps it untouched.
        """
        ctx = getattr(run, "context", None)
        if ctx is None or workflow is None:
            return
        existing = getattr(ctx, "topology", None)
        if existing is not None and (existing.adjacency or existing.edge_map):
            return
        ctx.topology = GraphTopology.from_workflow(workflow)

    # ------------------------------------------------------------------
    # Phase 3 Gate 3 — Executor routing
    # ------------------------------------------------------------------

    def _is_executor_mode(self) -> bool:
        """Return ``True`` iff ``EXECUTOR_MODE=executor`` — the standalone
        ExecutorConsumer pod is the authoritative handler invoker, so the
        engine SKIPS its inline ``handler.handle`` / ``handle_entry_nodes``
        path and emits ``NodeInput`` for the executor instead. See
        ``docs/design/phase-3-executor-extraction.md`` §10.4.
        """
        return self._executor_mode == "executor"

    def _emit_node_input_for_executor(
        self,
        *,
        node: Any,
        run: Any,
        workflow: Any,
        task: Any | None,
        iteration_index: int | None = None,
    ) -> None:
        """Publish a ``NodeInput`` envelope so the standalone Executor
        invokes the handler and emits ``NodeOutputProduced``.

        Idempotent under replay: ``event_id`` is derived from the
        task id (or node id when no task exists) so a re-emitted
        ``NodeInput`` collapses to the same event-log entry.
        """
        from app.layer1_domain.events import NodeInput as _NodeInputEvent
        task_id = getattr(task, "id", "") if task is not None else ""
        seed = task_id or node.id
        event_id = EventId.derive(seed, f"executor_input:{node.id}")
        envelope = _NodeInputEvent(
            event_id=event_id,
            run_id=run.id,
            workflow_id=run.workflow_id,
            node_id=node.id,
            task_id=task_id,
            resolved_input={
                k: v for k, v in (getattr(task, "resolved_input", None) or {}).items()
                if k != "__credentials"
            },
            iteration_index=iteration_index,
            root_run_id=run.root_run_id or run.id,
            depth=run.depth_level,
            source="workflow-control-plane",
        )
        # Publish directly via ``_publish_now`` so the executor sees the input
        # without waiting for a drain — under executor mode we return before
        # reaching one. (``publish_event`` would also publish immediately now;
        # ``_publish_now`` is used to stay independent of ``self._emitter``.)
        self._publish_now(EventTopic.NODE_INPUT, envelope)

    def set_queue(self, queue: Any) -> None:
        """Inject the orchestration queue (breaks circular dependency with bootstrap)."""
        self._queue = queue

    # ------------------------------------------------------------------
    # ResolutionScope contextvar helpers.
    #
    # ``orchestrate_start`` / ``_continue`` / ``_fail`` / ``_start_child_workflow``
    # all wrap their inner logic with ``set_current_run_id`` so the
    # file-backed resolver caches are run-scoped. They now also wrap
    # with ``set_current_scope`` so the resolver overlay can answer
    # ``$parent`` / ``$root`` / ``$variable`` and per-iteration
    # ``$loop`` lookups without touching the 13 handler call sites.
    #
    # ``scope_builder`` is optional: when None (legacy / direct-test
    # construction), these helpers only set the run-id contextvar —
    # behaviour identical to pre-ResolutionScope code.
    # ------------------------------------------------------------------

    # Run-scope lifecycle (lock + contextvar + scope-chain building) lives
    # on ``self._scope`` (RunScopeManager). The engine retains thin
    # private wrappers for the few sites that still call them inline; new
    # code should call ``self._scope.*`` directly.

    def _enter_run_scope(
        self,
        run_id: str,
        run: WorkflowRun | None = None,
    ) -> tuple[Any, Any]:
        """Install the run-scope contextvars.

        **Always pass ``run`` when you already have it.** Without it,
        ``RunScopeManager.enter`` falls back to the SYNCHRONOUS
        ``run_repo.get_by_id`` — a blocking hdbcli round-trip with no thread
        hop, executed directly on the event loop. Every ``orchestrate_*``
        entry point used to do exactly that and then immediately ``await
        aget_by_id(run_id)`` for the same row, so each action paid for the
        row twice and one of those payments froze the whole pod: HTTP,
        Socket.IO, the broker consumers and the other orchestration workers
        alike. Measured at 5 ms/round-trip, 16 concurrent top-level actions
        stalled the loop for 169 ms and gained nothing from
        ``ORCHESTRATION_WORKER_COUNT=4`` — wall-clock equalled the serial
        cost exactly.

        Passing ``run`` also removes a quieter defect: the scope was built
        from a *different read* of the run than the orchestration body then
        operated on, so the two could observe different versions of the row.
        """
        return self._scope.enter(run_id, run)

    @staticmethod
    def _exit_run_scope(tokens: tuple[Any, Any]) -> None:
        RunScopeManager.exit(tokens)

    # ------------------------------------------------------------------
    # Phase 2a — view-store read helpers.
    #
    # Engine orchestration reads should go through ``IRunContextViewStore``
    # (projector-backed under ``PROJECTOR_READ_MODE=projector``) rather
    # than directly off the in-memory aggregate. The engine still mutates
    # the aggregate in-flight (Phase 3b deletes those writes); when a
    # read is on the same call frame as an engine-local mutation that the
    # projector has not seen yet, the helpers fall back to aggregate.
    # Callers that need a just-written value use the ``extra_completed``
    # / ``extra_skipped`` parameters.
    # ------------------------------------------------------------------

    def _view_completed(
        self, run: WorkflowRun, extra: frozenset[str] = frozenset(),
    ) -> frozenset[str]:
        # Always union the in-memory aggregate so a just-completed node
        # (mutated synchronously in this call frame) is visible even if
        # the projector hasn't applied the NodeCompleted event yet.
        # Without this, ``check_run_completion`` can miss a builtin leaf
        # that just finished and leave the run stuck in RUNNING.
        in_memory = frozenset(run.context.completed_nodes)
        if self._view_store is not None:
            view = self._view_store.get(run.id)
            if view is not None:
                return view.completed_nodes | in_memory | extra
        return in_memory | extra

    def _view_skipped(
        self, run: WorkflowRun, extra: frozenset[str] = frozenset(),
    ) -> frozenset[str]:
        in_memory = frozenset(run.context.skipped_nodes)
        if self._view_store is not None:
            view = self._view_store.get(run.id)
            if view is not None:
                return view.skipped_nodes | in_memory | extra
        return in_memory | extra

    def _view_variables(self, run: WorkflowRun) -> "Mapping[str, Any]":
        if self._view_store is not None:
            view = self._view_store.get(run.id)
            if view is not None:
                return view.variables
        return run.context.variables

    async def _enqueue_or_dispatch(
        self, task: Task, run: WorkflowRun, workflow: Workflow, topology: Any,
    ) -> None:
        """Enqueue an ``ExecuteNodeAction`` for ``task``.

        The ``workflow`` and ``topology`` parameters are kept for callers
        that still pass them; the worker re-fetches everything by id.
        """
        await self._queue.enqueue(ExecuteNodeAction(run_id=run.id, task_id=task.id))

    async def _active_task(
        self, run_id: str, node_id: str, node_type: NodeType,
    ) -> Any | None:
        """Edge-based dispatch coalescing — return the in-flight task, if any.

        Returns the node's latest task when it is in a non-terminal
        status (the engine should NOT create a new task — the arrival
        coalesces into it), else ``None``. Without this, a node with
        multiple incoming edges would receive one task per arriving
        edge *concurrently*.

        SA-846: a COMPLETED latest task is NOT a reason to skip anymore —
        an edge taken into a completed node re-runs it (cycle re-entry /
        late second arrival); the visit counter on ``run.context``
        gives the re-run a fresh task id. Only in-flight statuses
        coalesce — and callers use :meth:`_maybe_queue_revisit` so an
        arrival during a STARTED task is deferred, not dropped (Mode A).

        MERGE and PARALLEL are exempt: each predecessor arrival is meant
        to re-invoke the handler so it can accumulate state (quorum
        buffer, branch evaluation). CONDITION and SWITCH are exempt
        because they can be legitimately revisited in cyclic graphs
        (e.g. a loop that routes back through a condition node).

        SA-2318 — this asks about ONE node, so it reads one row. It used to
        call ``alist_by_run`` and scan in Python: every task of the run read
        and deserialized, to return one of them. The caller is the fan-out loop
        in ``process_and_dispatch_successors``, so that ran once per successor
        on every node completion, and the run's task count grows as the run
        progresses — making a completion cost O(successors x tasks_in_run) and
        a whole run quadratic in its node count. A 500-node workflow, or a loop
        body re-entered 500 times, is exactly where that dominates.

        The repository method is the same selection expressed as a query, so
        the semantics are unchanged; see ``ITaskRepository.latest_by_run_node``
        for the tie-breaking and NULL-timestamp contract.
        """
        if node_type in (NodeType.MERGE, NodeType.PARALLEL, NodeType.CONDITION, NodeType.SWITCH):
            return None
        latest: Any = await self._task_repo.alatest_by_run_node(run_id, node_id)
        if latest is None:
            return None
        non_terminal = {
            TaskStatus.PENDING, TaskStatus.WAITING, TaskStatus.READY,
            TaskStatus.RUNNING, TaskStatus.RETRYING,
        }
        return latest if latest.status in non_terminal else None

    def _maybe_queue_revisit(
        self, run: WorkflowRun, node_id: str, active_task: Any,
        scope_stack: Any = None,
    ) -> None:
        """SA-846 Mode A — defer (don't drop) an arrival into a busy node.

        A taken edge landed while the node's latest task is in flight:

        * READY — the task hasn't started; it will see this arrival's
          predecessor state when it runs, so coalescing is lossless.
        * STARTED (RUNNING / PENDING / WAITING / RETRYING) — the in-flight
          execution can't observe the new arrival (e.g. a WORKFLOW node's
          tracking task stays RUNNING for the whole child-run duration).
          Record a pending revisit; ``check_run_completion`` drains it
          once the node quiesces, so the node re-runs exactly once per
          window instead of the arrival being silently lost.

        Loop-scoped arrivals keep the legacy coalesce: a deferred revisit
        task would carry no scope_stack and corrupt iteration accounting.
        """
        if active_task.status == TaskStatus.READY:
            return
        if scope_stack:
            return
        run.context.pending_revisits.add(node_id)

    async def _drain_pending_revisits(
        self, run: WorkflowRun, workflow: Workflow, topology: Any,
    ) -> bool:
        """Dispatch deferred revisits whose node has quiesced.

        Called by ``check_run_completion`` before it declares the run
        complete, so a queued revisit always re-runs its node before the
        run can terminate. Nodes still in flight stay queued for a later
        check. Returns True when at least one revisit task was dispatched
        (the run is then by definition not quiescent).
        """
        dispatched = False
        for node_id in sorted(run.context.pending_revisits):
            node = workflow.get_node(node_id)
            if node is None:
                run.context.pending_revisits.discard(node_id)
                continue
            if await self._active_task(run.id, node_id, node.node_type) is not None:
                continue
            run.context.pending_revisits.discard(node_id)
            # Same semantics as a live taken edge into a terminal node:
            # un-skip + visit bump so the re-run gets a fresh -v{n} id.
            run.context.skipped_nodes.discard(node_id)
            visits = run.context.node_visits
            visits[node_id] = visits.get(node_id, 0) + 1
            discard_stale_node_variable(run, node)
            new_task = self._task_factory.create_task(node, run)
            await self._task_repo.asave(new_task)
            await self._enqueue_or_dispatch(new_task, run, workflow, topology)
            logging.getLogger("OrchestrationEngine").info(
                "Drained pending revisit: run=%s node=%s task=%s",
                run.id, node_id, new_task.id,
            )
            dispatched = True
        if dispatched:
            await self._run_repo.asave(run)
        return dispatched

    async def maybe_enqueue_loop_iteration(
        self, completed_task: Task, run: WorkflowRun,
    ) -> None:
        """Body-completion hook for event-driven loops. Delegates to
        :class:`LoopIterationDriver`. Public name kept because the
        ``_exec_uniform`` postamble + several test mocks bind to it."""
        await self._loop_driver.maybe_enqueue_loop_iteration(
            completed_task, run,
        )

    def _get_run_lock(self, run_id: str) -> asyncio.Lock:
        return self._scope.get_lock(run_id)

    @asynccontextmanager
    async def locked_run(self, run_id: str) -> AsyncIterator[None]:
        """Reentrant per-run critical section.

        ``asyncio.Lock`` is not reentrant, but run-scoped work nests: a
        WORKFLOW node starts its child inline under the parent's lock
        (``ChildWorkflowCoordinator.start``), and a trivially-empty child
        can complete synchronously in that same stack — its
        ``complete_parent`` then needs the parent's lock that this very
        task already holds. The held set is tracked in a ``ContextVar``
        (per asyncio task, propagated through the call stack), so a
        nested ``locked_run`` for an already-held run id is a no-op while
        a cross-task acquisition serializes normally.

        Lock ordering is strictly child → parent (upward): the
        coordinator acquires the parent's lock while the child's may be
        held, and no path acquires a child's lock while holding the
        parent's (child starts enqueue/scope-switch without locking the
        child id). The ancestry-cycle check keeps the parent graph
        acyclic, so upward-only acquisition cannot deadlock.
        """
        held = _held_run_locks.get()
        if run_id in held:
            yield
            return
        async with self._get_run_lock(run_id):
            token = _held_run_locks.set(held | {run_id})
            try:
                yield
            finally:
                _held_run_locks.reset(token)

    @asynccontextmanager
    async def locked_run_for_write(self, run_id: str) -> AsyncIterator[None]:
        """Run lock for aggregate WRITERS, acquired only at the outermost
        boundary. Wired into ``run_save_retry.set_lock_provider``.

        The plain :meth:`locked_run` is re-entrant for the SAME run id only.
        That is not enough here, because a CAS write can be reached while a
        DIFFERENT run's lock is held — ``ChildWorkflowCoordinator.start`` runs
        ``check_run_completion(child_run)`` inside the PARENT's section, and
        that bottoms out in ``_finalize`` -> ``save_run_with_cas_retry`` on the
        CHILD. Taking the child's lock there would be a downward acquisition,
        the one direction ``locked_run``'s ordering rule forbids, and it
        deadlocks against the legitimate upward path:

            task A  holds parent, wants child   (coordinator.start)
            task B  holds child,  wants parent  (complete_parent / fail_parent)

        So: acquire only when this task holds no run lock at all. A caller that
        already holds one is inside a run-scoped critical section and keeps
        exactly the behaviour it had before — which is what the ordering rule
        is there to guarantee. The writers this actually closes over
        (``CompleteTaskUseCase``, cancel, pause, resume, trigger-node, the
        sweeper) are all top-level entry points holding nothing, so they always
        take it.

        Acquiring only from the empty state makes run-lock deadlock impossible
        by construction: no task ever waits on a second run lock.
        """
        if _held_run_locks.get():
            yield
            return
        async with self.locked_run(run_id):
            yield

    def drop_run_resolver_cache(self, run_id: str) -> None:
        """Public hook for use cases that transition a run to a terminal
        state outside the normal ``check_run_completion`` path
        (e.g., ``cancel_run``). Delegates to the scope manager.

        Uses ``release_run`` so a CANCELLED run frees the projector view
        store's three per-run dicts too, not just the resolver strategies
        (SA-2258)."""
        self._scope.release_run(run_id)

    # ------------------------------------------------------------------
    # Event-publishing primitives.
    # ------------------------------------------------------------------
    # Two operations, two pairs of names:
    #   * DIRECT (``publish_event`` / ``publish_events``, via
    #     ``_publish_now*``) — reads ``self._event_publisher`` directly
    #     because tests construct OrchestrationEngine via ``__new__`` and
    #     inject only that attribute. Independent of ``self._emitter``.
    #   * BUFFERED (``emit`` / ``drain_events``) — routed through
    #     ``self._emitter`` (EventEmitter), which owns the per-async-task
    #     ``_collector_var`` and drains in priority order.

    def emit(self, topic: str, event: Any) -> None:
        """Buffer an event for publishing at the next drain boundary."""
        self._emitter.buffer(topic, event)

    async def drain_events(self) -> None:
        """Publish all collected events in priority order, then yield."""
        await self._emitter.drain()

    def _publish_now(self, topic: str, event: Any) -> None:
        """Publish a single event directly, bypassing the collector."""
        if self._event_publisher:
            self._event_publisher.publish(topic, event)

    def _publish_now_many(self, events: list) -> None:
        """Publish a list of events directly, bypassing the collector."""
        if not self._event_publisher:
            return
        from app.layer1_domain.events import topic_for_event
        for event in events:
            self._event_publisher.publish(topic_for_event(event), event)

    @staticmethod
    def _enrich_event(event: Any, run: WorkflowRun) -> Any:
        """Set root_run_id and depth on a domain event from the run context."""
        if hasattr(event, "root_run_id") and not event.root_run_id:
            event.root_run_id = run.root_run_id or run.id
        if hasattr(event, "depth"):
            event.depth = run.depth_level
        return event

    @staticmethod
    def _format_node_failure_error(task: Any, workflow: Any) -> str:
        """Build a run-failure message that names the failing node.

        Used wherever a run is marked FAILED so the error that surfaces on the
        run AND propagates to a parent WORKFLOW node (via ``run.error`` →
        ``fail_parent``) carries the node's *name* (not a bare UUID) and the
        real underlying cause instead of ``unknown``.
        """
        raw = (getattr(task, "error", None) or "unknown") if task else "unknown"
        node = (
            workflow.get_node(task.node_id)
            if (workflow is not None and task and getattr(task, "node_id", None))
            else None
        )
        label = node.name if (node and node.name) else (getattr(task, "node_id", "") if task else "")
        return f"Node '{label}' failed: {raw}" if label else raw

    async def _mark_task_failed(
        self,
        task: Task,
        node: Any,
        run: WorkflowRun,
        error: str,
        error_code: str = "ExpressionError",
    ) -> None:
        """Mark a task as FAILED due to a handler error and emit NodeFailed."""
        logging.getLogger(__name__).warning(
            "Handler error for node %s (%s): %s — marking task as FAILED",
            node.id, node.node_type.value, error,
        )
        now = datetime.now(timezone.utc).isoformat()
        task.status = TaskStatus.FAILED
        task.error = error
        task.completed_at = now
        # Mirror the error code into output_metadata — the same place the
        # worker-failure path (FailTaskUseCase) stashes it — so the read
        # APIs (GET /nodes/{id}/execution, GET /nodes/{id}/output, GET /data
        # node_error) can surface a code for handler/expression failures, not
        # just the bare message. Task-local metadata only: no event, no
        # routing change.
        task.output_metadata = {
            **(task.output_metadata or {}),
            "error_code": error_code,
        }
        await self._task_repo.asave(task)
        run.context.completed_nodes.add(node.id)
        self.emit(
            EventTopic.NODE_FAILED,
            NodeFailed(
                task_id=task.id,
                run_id=run.id,
                workflow_id=run.workflow_id,
                node_id=node.id,
                error_code=error_code,
                error_message=error,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
            ),
        )

    async def _mark_task_completed(
        self,
        task: Task,
        run: WorkflowRun,
    ) -> None:
        """Drive a tracking task to COMPLETED + emit NodeCompleted — the
        success mirror of :meth:`_mark_task_failed`.

        The LOOP node's tracking task is left READY by the loop-body runner
        (``_run_loop_body`` only ships the body tasks; it never transitions
        the loop task itself). When the loop finishes every iteration the
        task must be driven terminal, or:

        * ``WorkflowRun.decide_completion`` keeps seeing a non-terminal task
          for the loop node and the run never quiesces (stays RUNNING), and
        * ``aggregate_node_status`` reports the loop node stuck ``READY`` on
          the canvas (the read path is task-based, not view-based).

        Idempotent: a task already COMPLETED is left untouched — the
        orchestration queue is at-least-once, so the all-done branch can
        fire more than once for the final iteration. ``NodeCompleted`` does
        NOT trigger edge routing (the DispatcherConsumer routes on
        ``NodeOutputProduced`` / ``LoopExitDispatched``, not this topic), so
        emitting it here cannot double-dispatch the loop's exit edge.
        """
        if task.status == TaskStatus.COMPLETED:
            return
        now = datetime.now(timezone.utc).isoformat()
        if not task.started_at:
            task.started_at = now
        task.status = TaskStatus.COMPLETED
        task.completed_at = now
        await self._task_repo.asave(task)
        run.context.completed_nodes.add(task.node_id)
        self._publish_now(
            EventTopic.NODE_COMPLETED,
            NodeCompleted(
                task_id=task.id,
                run_id=run.id,
                workflow_id=run.workflow_id,
                node_id=task.node_id,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
                # The worker's own runtime, as it reported it. The field has
                # existed on this event since it was written and was never
                # populated on the run path, so ``NodeDispatched ->
                # NodeOutputProduced`` — 58.6% of a run's wall clock, measured
                # 2026-08-11 — could not be split into "the worker" and "the
                # path to the worker". Now the event log carries both ends and
                # the interior.
                execution_time_ms=task.execution_time_ms or 0,
            ),
        )

    async def rerequest_pending_node(
        self,
        node: Any,
        run: WorkflowRun,
        workflow: Workflow,
        topology: Any,
    ) -> "NodeHandlerResult | None":
        """Re-run a PENDING_NODE_TYPES handler from scratch and fire its
        ``*.requested`` event again.

        Used by the run-level replay flow when an external system reconnects
        and needs the original request event re-emitted (with potentially
        refreshed schema if upstream data changed). The caller is responsible
        for cancelling any pre-existing PENDING task on this node before
        invoking — this method only creates the *new* PENDING task and emits
        the fresh event.

        Returns the ``NodeHandlerResult`` (so callers can inspect the new
        task id) or ``None`` if the node type isn't a PENDING type or no
        handler is registered.
        """
        if node.node_type not in PENDING_NODE_TYPES:
            return None
        handler = self._handler_registry.get(node.node_type)
        if handler is None:
            return None

        # Same hydration as ``_exec_uniform``: surface completed predecessor
        # outputs from the projector view so a re-requested DATA_EDIT (or any
        # pending node whose input_binding references upstream nodes) can
        # resolve ``{{$<predecessor>.field}}`` paths.
        if self._view_store is not None:
            try:
                view = self._view_store.get(run.id)
            except Exception:
                view = None
            if view is not None:
                ctx_vars = run.context.variables
                for vk, vv in view.variables.items():
                    ctx_vars.setdefault(vk, vv)

        # Activate the run ResolutionScope (mirrors ``_exec_uniform``):
        # a re-requested PENDING node whose prefill / input_binding
        # references an ambient-only namespace (``{{$variables.x}}`` /
        # ``$variable`` / ``$parent`` / ``$root``) would otherwise fail to
        # resolve on this replay path — the view→aggregate hydration above
        # only covers concrete ``{{$<node>.field}}`` dict keys. Built after
        # that hydration so the ``SystemVariablesFrame`` sees ``__vars``.
        rereq_scope_tokens = None
        if get_current_scope() is None:
            try:
                rereq_scope_tokens = self._enter_run_scope(run.id, run)
            except Exception:
                rereq_scope_tokens = None
        try:
            handler_result = await handler.handle(
                node=node,
                run=run,
                workflow=workflow,
                topology=topology,
                completed_task=None,
                task_factory=self._task_factory,
            )
        finally:
            if rereq_scope_tokens is not None:
                self._exit_run_scope(rereq_scope_tokens)

        for pending_task in handler_result.tasks_to_dispatch:
            await self._task_repo.asave(pending_task)
            self._stage_recorder.record(
                run_id=run.id,
                node_id=pending_task.node_id,
                task_id=pending_task.id,
                event_type="task.pending_input",
                from_status=TaskStatus.READY.value,
                to_status=TaskStatus.PENDING.value,
                metadata={"node_type": node.node_type.value, "replay": True},
            )

        self.publish_events(handler_result.events)
        return handler_result

    async def check_run_completion(
        self, run: WorkflowRun, workflow: Workflow
    ) -> None:
        """Drive the run's quiescence decision (the I/O shell).

        Edge-based dispatch: completion is decided by quiescence — no
        non-terminal tasks AND no failed tasks. The "all leaves covered"
        gate is gone alongside the downstream-skip BFS that fed it; with
        edge-based dispatch a leaf either has a completed task (it ran)
        or no task at all (no incoming edge fired), and we no longer
        manufacture skipped entries to satisfy a leaves-covered check.

        This method is the imperative shell: it resolves facts (I/O), asks
        the aggregate for the pure :class:`CompletionDecision`, then performs
        the I/O that decision implies. The branching judgement lives in
        :meth:`WorkflowRun.decide_completion`.
        """
        # Allow re-evaluation for FAILED runs — a rerun or late completion
        # may have resolved all failures, so the run can recover. Terminal
        # (no-recovery) runs short-circuit before any I/O.
        terminal_no_recovery = {RunStatus.COMPLETED, RunStatus.CANCELLED}
        if run.status in terminal_no_recovery:
            return

        topology = GraphTopology.from_workflow(workflow)
        run.context.topology = topology

        all_tasks = await self._task_repo.alist_by_run(run.id)
        current_node_ids = {n.id for n in workflow.nodes}
        all_tasks = [t for t in all_tasks if t.node_id in current_node_ids]

        # Use only the latest task per node to avoid stale FAILED tasks
        # from previous attempts (rerun creates new tasks for the same node).
        latest_per_node: dict[str, Any] = {}
        for t in all_tasks:
            existing = latest_per_node.get(t.node_id)
            if existing is None or (t.created_at or "") > (existing.created_at or ""):
                latest_per_node[t.node_id] = t
        latest_tasks = list(latest_per_node.values())

        decision = run.decide_completion(
            latest_tasks, view_skipped=self._view_skipped(run),
        )
        logging.getLogger("OrchestrationEngine").debug(
            "check_run_completion: run=%s decision=%s", run.id, decision.kind,
        )

        if decision.kind == "failed":
            failed_task = next(
                (t for t in latest_tasks if t.id == decision.failed_task_id), None,
            )
            # Carry the real cause + failing node name onto ``run.error`` so a
            # parent WORKFLOW node sees it instead of ``unknown`` (``fail_parent``
            # reads the entity field, not an event).
            error = self._format_node_failure_error(failed_task, workflow)
            await self._finalize(
                run, RunStatus.FAILED,
                error=error, failed_task_id=decision.failed_task_id,
            )
            return

        # SA-846 Mode A — before applying a quiescence outcome, dispatch any
        # deferred revisits whose node has gone idle. An arrival that landed
        # while the node's task was in flight re-runs the node here instead of
        # being silently dropped; dispatching makes the run non-quiescent, so
        # the outcome below is skipped this round. When the drain dispatches
        # nothing the pre-drain decision still applies (the task facts are
        # unchanged) — so we fall through to it rather than returning.
        if run.context.pending_revisits:
            if await self._drain_pending_revisits(run, workflow, topology):
                return

        if decision.kind == "completed":
            output_data = self._build_output_data(run, workflow)
            await self._finalize(
                run, RunStatus.COMPLETED,
                output_data=output_data, total_tasks=len(all_tasks),
            )
        elif decision.kind == "pending":
            await self._finalize(run, RunStatus.PENDING)
        # decision.kind == "none": run still has live work — nothing to do.

    async def _finalize(
        self,
        run: WorkflowRun,
        status: RunStatus,
        *,
        error: str = "",
        failed_task_id: str = "",
        output_data: dict[str, Any] | None = None,
        total_tasks: int = 0,
    ) -> None:
        """Apply a terminal/pending transition decided by
        :meth:`WorkflowRun.decide_completion`. The single site that performs
        CAS-save + scope release/drop + stage record + status emit + callback
        + parent-notify. Pure I/O — it holds no completion judgement.

        One formatted ``error`` / ``output_data`` feeds the in-memory run, the
        CAS retry closure, and the emitted event, so they never diverge.
        """
        now = datetime.now(timezone.utc).isoformat()

        if status == RunStatus.FAILED:
            run.status = RunStatus.FAILED
            run.error = error
            run.completed_at = now

            def _apply(r: WorkflowRun) -> None:
                r.status = RunStatus.FAILED
                r.error = error
                r.completed_at = now

        elif status == RunStatus.COMPLETED:
            run.status = RunStatus.COMPLETED
            run.completed_at = now
            run.output_data = output_data
            # Clear any error left by a prior FAILED evaluation — a run can
            # recover (FAILED → COMPLETED) when late completions resolve all
            # failures, and a stale failure message must not survive.
            run.error = ""

            def _apply(r: WorkflowRun) -> None:
                r.status = RunStatus.COMPLETED
                r.completed_at = now
                r.output_data = output_data
                r.error = ""

        else:  # RunStatus.PENDING
            run.status = RunStatus.PENDING

            def _apply(r: WorkflowRun) -> None:
                r.status = RunStatus.PENDING

        await save_run_with_cas_retry(self._run_repo, run.id, _apply)

        # Terminal runs release the run scope (lock + resolver cache); a
        # PENDING run stays live (awaiting user input) and keeps both.
        if status in (RunStatus.FAILED, RunStatus.COMPLETED):
            self._scope.release_lock(run.id)
            self._scope.release_run(run.id)

        if status == RunStatus.FAILED:
            self._stage_recorder.record(
                run_id=run.id,
                event_type="run.failed",
                from_status=RunStatus.RUNNING.value,
                to_status=RunStatus.FAILED.value,
            )
            self.emit_run_status(
                run, "failed", error=error, failed_task_id=failed_task_id,
            )
            self.publish_callback(run, "FAILED")
            if run.parent_run_id and run.parent_node_id:
                await self.fail_parent_tracking_task(run)
        elif status == RunStatus.COMPLETED:
            self._stage_recorder.record(
                run_id=run.id,
                event_type="run.completed",
                from_status=RunStatus.RUNNING.value,
                to_status=RunStatus.COMPLETED.value,
                metadata={"total_tasks": total_tasks},
            )
            self.emit_run_status(
                run, "completed", output_data=run.output_data or {},
            )
            self.publish_callback(run, "COMPLETED")
            if run.parent_run_id and run.parent_node_id:
                await self._complete_parent_tracking_task(run)
        else:  # RunStatus.PENDING
            self._stage_recorder.record(
                run_id=run.id,
                event_type="run.pending",
                from_status=RunStatus.RUNNING.value,
                to_status=RunStatus.PENDING.value,
            )
            # Emit run.status=pending so the projector view ``status`` flips
            # from RUNNING to PENDING. Without this event the API
            # GET /runs/{id} (read-side) keeps returning RUNNING even though
            # the aggregate is PENDING.
            self.emit_run_status(run, "pending")

    async def _apply_csv_data_mode(self, task: Task, node: Any) -> None:
        """Convert form data to CSV and upload if data_mode requires it."""
        nd_snapshot = getattr(node, "definition_snapshot", None) or {}
        data_mode = nd_snapshot.get("data_mode", "json")
        edit_mode = nd_snapshot.get("edit_mode", "none")

        if data_mode == "json" or data_mode not in ("csv_single", "csv_multi"):
            return

        resolved = task.resolved_input or {}
        locked_defaults = nd_snapshot.get("locked_defaults", {})

        # If user already uploaded a file, skip CSV conversion
        if "file_id" in resolved:
            merged = {"file_id": resolved["file_id"], "data_mode": data_mode}
            merged.update(locked_defaults)
            task.resolved_input = merged
            if edit_mode == "before_dispatch":
                task.status = TaskStatus.PENDING
            return

        # Convert form data to CSV and upload
        if not self._csv_converter or not self._file_service_client:
            task.status = TaskStatus.FAILED
            task.error = "CSV converter or file service not configured"
            return

        csv_bytes, filename = self._csv_converter.determine_csv_input(
            resolved, locked_defaults, data_mode,
        )
        if not csv_bytes:
            task.status = TaskStatus.FAILED
            task.error = "Failed to generate CSV from input data"
            return

        upload_result = await self._file_service_client.upload_file(csv_bytes, filename)
        file_id = upload_result.get("file_id")
        if not file_id:
            task.status = TaskStatus.FAILED
            task.error = "File upload failed — no file_id returned"
            return

        merged = {"file_id": file_id, "data_mode": data_mode}
        merged.update(locked_defaults)
        task.resolved_input = merged

        if edit_mode == "before_dispatch":
            task.status = TaskStatus.PENDING

    def publish_callback(self, run: WorkflowRun, status: str) -> None:
        """Publish a workflow completion callback to the agent if configured."""
        if not (run.callback_topic and run.correlation_id and self._callback_publisher):
            return
        self._callback_publisher.publish(
            run.callback_topic,
            {
                "correlation_id": run.correlation_id,
                "run_id": run.id,
                "workflow_id": run.workflow_id,
                "status": status,
                "output_data": run.output_data or {},
                "error": run.error or "",
                "metadata": run.metadata or {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            key=run.correlation_id,
        )

    # Use-case-facing direct publish API: ``publish_event`` /
    # ``publish_events`` publish IMMEDIATELY, so an event can never strand
    # if a call site lacks a drain boundary.

    def publish_event(self, topic: str, event: Any) -> None:
        """Publish a single event immediately."""
        self._publish_now(topic, event)

    def publish_events(self, events: list) -> None:
        """Publish a list of events immediately."""
        self._publish_now_many(events)

    # ------------------------------------------------------------------
    # Semantic event-emission helpers
    # ------------------------------------------------------------------
    # These methods own event construction so use cases never need to import
    # EventTopic constants or event dataclasses directly.

    def emit_run_status(
        self,
        run: WorkflowRun,
        status: str,
        *,
        input_data: dict[str, Any] | None = None,
        output_data: dict[str, Any] | None = None,
        error: str = "",
        failed_task_id: str = "",
        cancelled_by: str = "",
        reason: str = "",
    ) -> None:
        """Emit the unified ``RunStatusChanged`` event.

        ``status`` is one of started / completed / failed / cancelled /
        paused / resumed / suspended / pending. One-shot transitions
        (started, cancelled) get a deterministic ``event_id`` so a
        re-emit on the same run dedupes in the log; repeatable ones
        (paused / resumed / pending) keep a random id.
        """
        id_kwargs: dict[str, Any] = {}
        if status == "started":
            id_kwargs["event_id"] = EventId.derive(run.id, "run_started")
            # Pin the event timestamp to run.started_at so a projector
            # replaying from the log produces byte-equal started_at with
            # the live aggregate.
            id_kwargs["timestamp"] = (
                run.started_at or datetime.now(timezone.utc).isoformat()
            )
        elif status == "cancelled":
            id_kwargs["event_id"] = EventId.derive(
                run.id, f"run_cancelled:{cancelled_by or 'system'}",
            )
        self._publish_now(
            EventTopic.RUN_STATUS_CHANGED,
            RunStatusChanged(
                run_id=run.id,
                workflow_id=run.workflow_id,
                status=status,
                input_data=dict(input_data or {}),
                output_data=dict(output_data or {}),
                error=error,
                failed_task_id=failed_task_id,
                cancelled_by=cancelled_by,
                reason=reason,
                completed_nodes=(
                    list(run.context.completed_nodes)
                    if status == "suspended" else []
                ),
                execution_mode=run.execution_mode if status == "suspended" else "",
                correlation_id=run.correlation_id,
                callback_topic=run.callback_topic,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
                **id_kwargs,
            ),
        )

    def emit_variables_seeded(
        self,
        run: WorkflowRun,
        state_updates: dict[str, Any],
        *,
        seed_id: str = "variables_seeded",
    ) -> None:
        """Emit NodeOutputProduced for bootstrap-seeded variables.

        ``StartRunUseCase`` writes metadata keys (``run``, ``workflow``,
        ``artifacts``, ``input``) into ``run.context.variables`` before
        any handler runs. The projector needs these to match the live
        aggregate; without an event, rebuilding from the log leaves
        ``variables`` empty.

        Phase 1a engine-split: this helper publishes the seed write-set
        as a ``NodeOutputProduced`` event with a ``__bootstrap__``
        source node id. The reducer's existing branch folds the
        ``state_updates`` into ``state.variables`` unchanged — the
        source-node distinction is irrelevant from the projection POV.
        The ``__bootstrap__`` source also makes ``DispatcherConsumer``
        skip fan-out (no ``EdgeTraversed`` / ``NodeInput``), so this is
        the fan-out-free way to land a write-set in the view.

        ``seed_id`` keys the deterministic event_id. The default suits
        the once-per-run bootstrap seed; callers that need a second,
        independently-deduped seed within the same run (e.g. ``test_node``
        mirroring an inline node output into the view after the reseed's
        bootstrap seed) pass a distinct value so the two events don't
        collide in the log.
        """
        if not state_updates:
            return
        # LAI-19: the sealed credential map must never enter a replayable
        # store. Filtered HERE, not at each caller — the natural way to seed a
        # run is ``dict(run.context.variables)``, five of the seven call sites
        # do exactly that, and only two of those five filtered (``start_run``
        # and ``child_workflow_coordinator``, each with its own inline
        # comprehension). A caller that forgets writes plaintext secrets into
        # AIW_EVENT_LOG permanently, and without an error. The two inline
        # filters stay: they are where it reads as documentation.
        #
        # Latent rather than live today — a reused test run reloads with an
        # empty ``variables`` because CONTEXT is gone, so there is nothing to
        # leak yet. That is exactly the kind of "safe for a reason that has
        # nothing to do with the rule" this closes.
        #
        # A copy, never a pop: the caller goes on using its dict, and popping
        # in place would delete the run's own credentials.
        state_updates = {
            k: v for k, v in state_updates.items() if k != "__credentials"
        }
        if not state_updates:
            return
        event_id = EventId.derive(run.id, seed_id)
        self._publish_now(
            EventTopic.NODE_OUTPUT_PRODUCED,
            NodeOutputProduced(
                event_id=event_id,
                run_id=run.id,
                workflow_id=run.workflow_id,
                node_id="__bootstrap__",
                state_updates=state_updates,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
            ),
        )

    def emit_node_cancelled(
        self,
        run: WorkflowRun,
        *,
        task: Task | None = None,
        node_id: str = "",
        cancelled_by: str = "",
        reason: str = "",
    ) -> None:
        """Emit ``NodeCancelled`` — covers both cancel and skip.

        Pass ``task`` for a real cancel (direct or cascaded); pass
        ``node_id`` with ``reason="skipped"`` / ``"branch_not_taken"``
        for a skipped node that never produced a task. The projector
        consumes this to mark the node terminal — folded into
        ``skipped_nodes`` for skip reasons, ``completed_nodes``
        otherwise. Deterministic event_id so redelivery / replay
        dedupes.

        The no-task correlation key includes the node's visit counter:
        a cycle can skip the same node again on a later visit (after an
        ``EdgeTraversed`` un-skip fold removed the first skip from the
        projected view), and without the visit the re-skip event would
        dedupe against the visit-1 ``NodeCancelled`` and never reach the
        log. Task-path ids are already visit-unique via the task id.
        """
        resolved_node_id = task.node_id if task is not None else node_id
        if task is not None:
            causation = task.id
            correlation = (
                f"node_cancelled:{resolved_node_id}:{cancelled_by or 'system'}"
            )
        else:
            causation = run.id
            visit = run.context.node_visits.get(resolved_node_id, 0)
            correlation = (
                f"node_cancelled:{resolved_node_id}:"
                f"{cancelled_by or 'system'}:v{visit}"
            )
        self._publish_now(
            EventTopic.NODE_CANCELLED,
            NodeCancelled(
                event_id=EventId.derive(causation, correlation),
                task_id=task.id if task is not None else "",
                run_id=run.id,
                workflow_id=run.workflow_id,
                node_id=resolved_node_id,
                cancelled_by=cancelled_by,
                reason=reason,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
            ),
        )

    def emit_child_run_cancelled(
        self,
        parent_run: WorkflowRun,
        child_run: WorkflowRun,
        *,
        reason: str = "",
    ) -> None:
        """Announce that a child workflow run was cancelled — parent-scoped.

        Emitted by the parent-cancellation cascade so the FE can mark the
        parent's WORKFLOW node cancelled alongside the child run.
        """
        self._publish_now(
            EventTopic.CHILD_RUN_CANCELLED,
            ChildRunCancelled(
                parent_run_id=parent_run.id,
                parent_node_id=child_run.parent_node_id or "",
                child_run_id=child_run.id,
                child_workflow_id=child_run.workflow_id,
                reason=reason,
                root_run_id=parent_run.root_run_id or parent_run.id,
                depth=parent_run.depth_level,
                source="workflow-control-plane",
            ),
        )

    def emit_node_data_edited(
        self,
        run: WorkflowRun,
        workflow: Any,
        *,
        node_id: str,
        new_ref: Any,
        task_id: str = "",
        edited_by: str = "",
        reason: str = "",
    ) -> None:
        """Emit ``NodeDataEdited`` with the ``new_ref`` write-set.

        Post Phase 3b.2a this is the *sole* write path for user-edited
        node data: the aggregate is no longer mutated. The payload
        carries ``state_updates`` (primary key plus name alias) so the
        projector reducer applies it via the same code path as
        ``NodeOutputProduced``; the projector-backed view store is the
        authoritative read side for subsequent GET / PATCH calls.
        """
        from app.layer1_domain.events import NodeDataEdited
        node = workflow.get_node(node_id) if workflow is not None else None
        node_name = node.name if node is not None and node.name else ""
        state_updates: dict[str, Any] = {node_id: new_ref}
        if node_name and node_name != node_id:
            state_updates[node_name] = new_ref
        # Include the new_ref's monotonic ``version`` in the correlation
        # key. Without it every PATCH on the same (task, node) produces
        # an identical event_id and subsequent edits are silently dropped
        # by the event log's event_id dedupe — the projector never folds
        # them, the view store lags behind the PATCH response's etag,
        # and the next PATCH sees the stale state → 412 ETag mismatch.
        ref_version = ""
        if isinstance(new_ref, dict):
            ref_version = str(new_ref.get("version", "") or "")
        self._publish_now(
            EventTopic.NODE_DATA_EDITED,
            NodeDataEdited(
                event_id=EventId.derive(
                    task_id or run.id,
                    f"node_data_edited:{node_id}:v{ref_version}",
                ),
                run_id=run.id,
                workflow_id=run.workflow_id,
                node_id=node_id,
                node_name=node_name,
                task_id=task_id,
                state_updates=state_updates,
                edited_by=edited_by,
                reason=reason,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
            ),
        )

    def emit_node_dispatched(
        self,
        task: Task,
        run: WorkflowRun,
        node: Any = None,
        *,
        attempt: int = 1,
        dispatch_reason: str = "",
        input: Any | None = None,
        buffer: bool = False,
        dispatch_to_worker: bool = True,
    ) -> None:
        """Emit ``NodeDispatched`` for any node execution attempt.

        ``dispatch_reason`` folds the former task.retrying / task.resumed /
        task.rerun / node.triggered events — pass ``"retry"``,
        ``"resume"``, ``"rerun"`` or ``"manual_trigger"``; the default
        ``""`` is a first dispatch.

        ``input`` defaults to ``task.resolved_input or {}``. Pass
        ``input={}`` explicitly for completion modes (immediate / pending /
        child_run / loop_body) where the handler's resolved input is
        internal-only and shouldn't appear in the dispatch payload.

        ``buffer=True`` routes through the EventCollector (used by
        child_run / loop_body so the dispatched event drains in priority
        order alongside the rest of the orchestration pass); ``buffer=False``
        publishes directly via ``_publish_now``.

        ``dispatch_to_worker=False`` makes this a **lifecycle-only** dispatch:
        the node still appears on the canvas, but no transport forwards the
        task to a worker. Pass it whenever the event exists to *show* a node
        rather than to *run* one — most importantly from the ``ExpressionError``
        handler in ``_exec_uniform``, where the task has no resolved input at
        all. Sending that to a worker is tenant-1 run ``c9ef6ca1``: the worker
        failed on empty input and its message overwrote the control plane's,
        so the error every human read named the wrong field.
        """
        iteration_index = (
            task.scope_stack[-1].iter if task.scope_stack else None
        )
        # No-``extracted`` model: file_refs are already pointer-only, so the
        # worker input needs no thinning — the worker SDK materialises via
        # ``file_id`` (``HttpFileRefResolver._fetch_file_data``).
        raw_input = (task.resolved_input or {}) if input is None else input
        event = NodeDispatched(
            task_id=task.id,
            run_id=task.run_id or run.id,
            workflow_id=run.workflow_id,
            node_id=task.node_id,
            node_name=node.name if node else "",
            worker_type=task.worker_type or "",
            # SA-1490 Phase 2: carry the pinned worker contract version resolved
            # at save time (``node.spec_version``) onto the dispatch event.
            spec_version=(getattr(node, "spec_version", None) or "") if node else "",
            # SA-2424: resolve the run's optional per-node instance pin (debug
            # routing) against THIS node. Empty when the run pinned nothing —
            # unchanged dispatch. Precedence: by_node -> by_worker_type.
            target_instance_id=resolve_target_instance_id(
                _pin_selection_for(run),
                task.node_id,
                task.worker_type,
            ),
            # SA-2437: host label carried alongside for the executor's host fallback.
            target_host=resolve_target_host(
                _pin_selection_for(run),
                task.node_id,
                task.worker_type,
            ),
            input=raw_input,
            node_config=node.config if node else {},
            idempotency_key=task.idempotency_key or "",
            correlation_id=run.id,
            attempt=attempt,
            dispatch_reason=dispatch_reason,
            root_run_id=run.root_run_id or run.id,
            depth=run.depth_level,
            source="workflow-control-plane",
            iteration_index=iteration_index,
            metadata=run.metadata or {},
            dispatch_to_worker=dispatch_to_worker,
        )
        if buffer:
            self.emit(EventTopic.NODE_DISPATCHED, event)
        else:
            self._publish_now(EventTopic.NODE_DISPATCHED, event)

    def emit_node_completed(
        self, task: Task | None, run: WorkflowRun, node: Any = None
    ) -> None:
        # ``_reseed_projector`` (test_node surgical reset) re-emits
        # NodeCompleted for surviving upstream nodes with ``task=None`` —
        # derive identity from ``node`` / ``run`` in that case. Mirrors the
        # ``task: Task | None`` guarding already present in
        # ``emit_node_cancelled``. The real-task path (CompleteTaskUseCase)
        # is unchanged: every field still comes from ``task``.
        if task is not None:
            task_id = task.id or ""
            run_id = task.run_id or ""
            node_id = task.node_id or ""
            data_type = "file_ref" if task.output_ref else "inline"
        else:
            task_id = ""
            run_id = run.id
            node_id = node.id if node is not None else ""
            data_type = "inline"
        try:
            self._publish_now(
                EventTopic.NODE_COMPLETED,
                NodeCompleted(
                    task_id=task_id,
                    run_id=run_id,
                    workflow_id=run.workflow_id or "",
                    node_id=node_id,
                    root_run_id=run.root_run_id or run.id,
                    depth=run.depth_level,
                    source="workflow-control-plane",
                ),
            )
            self._publish_now(
                EventTopic.CONTEXT_UPDATED,
                ContextUpdated(
                    run_id=run_id,
                    workflow_id=run.workflow_id or "",
                    node_id=node_id,
                    node_name=node.name if node else "",
                    data_type=data_type,
                    root_run_id=run.root_run_id or run.id,
                    depth=run.depth_level,
                    source="workflow-control-plane",
                ),
            )
        except Exception as e:
            logging.getLogger(__name__).error(
                "Failed to emit NodeCompleted for task %s: %s", task, e,
            )

    def emit_node_failed(
        self,
        task: Task,
        run: WorkflowRun,
        error_code: str = "WORKER_ERROR",
        error_msg: str = "",
    ) -> None:
        self._publish_now(
            EventTopic.NODE_FAILED,
            NodeFailed(
                task_id=task.id,
                run_id=task.run_id,
                workflow_id=run.workflow_id,
                node_id=task.node_id,
                error_code=error_code,
                error_message=error_msg,
                attempt=task.retry_count,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
            ),
        )

    def emit_pending_node_completed(
        self, task: Task, run: WorkflowRun, node: Any, action: str = "confirm"
    ) -> None:
        """Emit NODE_COMPLETED + the handler-specific secondary topic.

        Delegates the secondary payload to the node's handler via
        ``build_completion_event``. The NODE_COMPLETED topic always
        carries a ``NodeCompleted`` event so the projector reducer
        (which matches on event class name) folds the node into
        ``completed_nodes`` — without this the run would never satisfy
        ``all_leaves_covered`` and stay stuck non-terminal when a
        PENDING node is a leaf.
        """
        task_completed = NodeCompleted(
            task_id=task.id,
            run_id=task.run_id,
            workflow_id=run.workflow_id,
            node_id=task.node_id,
            root_run_id=run.root_run_id or run.id,
            depth=run.depth_level,
            source="workflow-control-plane",
        )
        self._publish_now(EventTopic.NODE_COMPLETED, task_completed)

        if node is None:
            # Defensive fallback: emit a bare InputSubmitted so the
            # secondary topic contract is still honoured.
            secondary = InputSubmitted(
                task_id=task.id,
                run_id=task.run_id,
                workflow_id=run.workflow_id,
                node_id=task.node_id,
                action=action,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
            )
            self._publish_now(EventTopic.INPUT_SUBMITTED, secondary)
            return

        handler = self._handler_registry.get(node.node_type)
        secondary_topic, secondary = handler.build_completion_event(
            task=task, run=run, node=node, action=action,
        )
        self._publish_now(secondary_topic, secondary)

    def emit_artifact_added(
        self,
        run: WorkflowRun,
        node_id: str,
        file_id: str,
        name: str,
        content_type: str,
        size_bytes: int,
        artifact_type: str,
    ) -> None:
        self._publish_now(
            EventTopic.ARTIFACT_ADDED,
            ArtifactAdded(
                run_id=run.id,
                workflow_id=run.workflow_id,
                node_id=node_id,
                file_id=file_id,
                name=name,
                content_type=content_type,
                artifact_type=artifact_type,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
            ),
        )

    def emit_node_input_edited(
        self,
        run: WorkflowRun,
        node_id: str,
        task_id: str,
        previous_file_id: str,
        new_file_id: str,
    ) -> None:
        self._publish_now(
            EventTopic.NODE_INPUT_EDITED,
            NodeInputEdited(
                run_id=run.id,
                workflow_id=run.workflow_id,
                node_id=node_id,
                task_id=task_id,
                previous_file_id=previous_file_id,
                new_file_id=new_file_id,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
            ),
        )

    async def handle_entry_nodes(
        self,
        entry_node_ids: list[str],
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        task_factory: TaskFactory,
    ) -> None:
        """Dispatch each entry node — inline for PENDING-category, async otherwise.

        PENDING-category nodes (INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD)
        run their handler synchronously here so by the time this returns
        auto-provisioning has uploaded artifact CSVs, NodeOutputProduced has
        projected into ``view.variables[node_id]``, and the (single) PENDING
        task carries ``resolved_input = provisioned_ref``. Without this
        inline path, callers hitting ``GET /data`` immediately after
        ``/test/full`` or ``POST /runs`` race the worker — they observe
        pre-provision state, capture an etag, and the next ``PATCH /data``
        sees a different working tree → 412 PRECONDITION_FAILED.

        The transient causation task constructed below is NOT persisted: it
        only carries ``task.id`` / ``idempotency_key`` for the ``NodeInput``
        and ``NodeOutputProduced`` events ``_exec_uniform`` emits. The
        handler creates the only task that lands in the repo (the PENDING
        one with ``resolved_input`` set) — eliminating the dual-task
        confusion where ``find_latest_task_for_node`` could otherwise
        return the empty placeholder.

        TASK / BUILTIN entry nodes keep the original async dispatch
        (placeholder task + ``ExecuteNodeAction`` enqueue) — they have
        nothing the FE can usefully read until the worker callback lands.
        """
        # The inline-vs-async judgement (per-entry, order-preserving) is the
        # pure ``WorkflowRun.decide_entry``. This shell owns only the dispatch
        # I/O each item implies: handler invocation for inline, task save +
        # enqueue for async.
        decision = run.decide_entry(entry_node_ids, workflow)
        for dispatch in decision.dispatches:
            node = workflow.get_node(dispatch.node_id)
            if node is None:  # decide_entry already filtered absent ids; defensive
                continue

            if dispatch.mode == "inline":
                causation_task = task_factory.create_task(node, run)
                await self._exec_uniform(
                    causation_task, node, run, workflow, topology,
                )
                continue

            entry_task = task_factory.create_task(node, run)
            await self._task_repo.asave(entry_task)
            await self._queue.enqueue(
                ExecuteNodeAction(run_id=run.id, task_id=entry_task.id),
            )

    async def process_successors(
        self,
        completed_task: Task,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
    ) -> ProcessingResult:
        """Resolve facts (I/O), make the pure routing decision on the run
        aggregate, then dispatch it.

        The decision itself — the port-override pre-routing for resumed
        PENDING nodes plus the ``FanoutDecision.compute`` fan-out — lives on
        the pure ``WorkflowRun.decide_successors``. This shell owns only the
        I/O around it: marking the source node completed and handing the
        decision to the ``SuccessorProcessor``.
        """
        # Ensure the source node is in completed_nodes before computing
        # fan-out — callers may not have added it yet (e.g. older code paths).
        run.context.completed_nodes.add(completed_task.node_id)

        decision = run.decide_successors(completed_task, workflow, topology)
        return await self._successor_processor.process(run, workflow, decision)

    @staticmethod
    def is_builtin_node(node_id: str, workflow: Workflow) -> bool:
        node = workflow.get_node(node_id)
        return node is not None and node.node_type in BUILTIN_NODE_TYPES

    # ------------------------------------------------------------------
    # Centralized successor processing (used by orchestrate_* and use cases)
    # ------------------------------------------------------------------

    async def process_and_dispatch_successors(
        self,
        completed_task: Task,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        test_mode: str | None = None,
    ) -> ProcessingResult:
        """Process successors, dispatch tasks, check run completion, and buffer events.

        This is the single centralized method that replaces the duplicated
        pattern across orchestrate_continue, orchestrate_fail, and the
        synchronous fallback paths in use cases.

        For manual execution mode, instead of dispatching successor tasks,
        this method transitions the run to SUSPENDED status.
        """
        processing_result = await self.process_successors(
            completed_task, run, workflow, topology
        )
        # Flush any events buffered during successor processing BEFORE the
        # direct publish below, so collected (priority-ordered) events keep
        # their slot ahead of these handler/processing events instead of
        # being overtaken by them on the wire.
        await self.drain_events()
        self._publish_now_many(processing_result.events)

        # Manual mode: skip auto-dispatch
        is_manual_mode = run.execution_mode == "manual"

        if test_mode != "node":
            for new_task in processing_result.tasks_to_dispatch:
                # Edge-based dispatch coalescing: a node with multiple
                # incoming edges receives one ``FanoutAction`` per edge
                # that gets taken; while one task is in flight the other
                # arrivals coalesce into it. A COMPLETED prior task does
                # NOT block — the successor processor already bumped the
                # visit counter, so ``new_task`` carries a fresh ``-v{n}``
                # id and re-runs the node (SA-846). MERGE/PARALLEL bypass
                # coalescing so they can accumulate per-arrival state
                # inside the handler.
                successor_node = workflow.get_node(new_task.node_id)
                if successor_node is not None:
                    active = await self._active_task(
                        run.id, new_task.node_id, successor_node.node_type,
                    )
                    if active is not None:
                        self._maybe_queue_revisit(
                            run, new_task.node_id, active, new_task.scope_stack,
                        )
                        continue
                await self._task_repo.asave(new_task)
                # Tasks already in a terminal status (e.g. FAILED due to
                # ExpressionError caught by SuccessorProcessor) were fully
                # handled during successor processing — just persist them.
                if new_task.status in {TaskStatus.FAILED, TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
                    continue
                if successor_node and successor_node.node_type in PENDING_NODE_TYPES and new_task.status == TaskStatus.PENDING:
                    # PENDING nodes whose handler already ran in the
                    # successor processor have status=PENDING. Just
                    # record the stage — do NOT re-enqueue to avoid
                    # double-emitting the requested event.
                    # Tasks still in READY status were produced by a BUILTIN
                    # handler (e.g. CONDITION/SWITCH) without running the
                    # PENDING handler, so they fall through to dispatch.
                    self._stage_recorder.record(
                        run_id=run.id, node_id=new_task.node_id,
                        task_id=new_task.id, event_type="task.pending_input",
                        from_status=TaskStatus.READY.value,
                        to_status=TaskStatus.PENDING.value,
                        metadata={"node_type": successor_node.node_type.value},
                    )
                elif is_manual_mode:
                    # Manual mode: skip auto-dispatch, user will trigger manually.
                    pass
                else:
                    if not self.is_builtin_node(new_task.node_id, workflow):
                        self._stage_recorder.record(
                            run_id=run.id, node_id=new_task.node_id,
                            task_id=new_task.id, event_type=EventTopic.NODE_DISPATCHED,
                            from_status="", to_status="READY",
                        )
                    await self._enqueue_or_dispatch(new_task, run, workflow, topology)

        # Failure-port handoff: if the just-completed task is in FAILED
        # state AND we dispatched at least one successor (the failure-port
        # edge fired), mark the failed task as ``skipped`` in
        # ``output_metadata`` so ``check_run_completion`` does not treat
        # the run as failed. The downstream recovery node continues the
        # run — the failure was handled by the workflow.
        if (
            completed_task.status == TaskStatus.FAILED
            and processing_result.tasks_to_dispatch
        ):
            md = dict(completed_task.output_metadata or {})
            md["skipped"] = True
            md.setdefault("reason", "failure_routed")
            completed_task.output_metadata = md
            await self._task_repo.asave(completed_task)

        await self._run_repo.asave(run)
        # Drain intermediate events (edge.traversed, condition.evaluated, etc.)
        # BEFORE checking completion so the frontend receives DAG lifecycle
        # events before a potential run.completed.
        await self.drain_events()
        await self.check_run_completion(run, workflow)
        # check_run_completion buffers RUN_COMPLETED / RUN_FAILED — flush so
        # the terminal event reaches the wire instead of stranding.
        await self.drain_events()

        # Manual mode: if run is not complete, transition back to SUSPENDED
        if is_manual_mode and run.status == RunStatus.RUNNING:
            run.status = RunStatus.SUSPENDED

            def _apply_suspended(r: WorkflowRun) -> None:
                r.status = RunStatus.SUSPENDED

            await save_run_with_cas_retry(self._run_repo, run.id, _apply_suspended)
            self._stage_recorder.record(
                run_id=run.id,
                event_type="run.suspended",
                from_status=RunStatus.RUNNING.value,
                to_status=RunStatus.SUSPENDED.value,
                metadata={"execution_mode": "manual"},
            )
            self.emit_run_status(run, "suspended")

        return processing_result

    # ------------------------------------------------------------------
    # High-level orchestration methods (called by background worker)
    # ------------------------------------------------------------------

    async def orchestrate_start(self, run_id: str) -> None:
        """Process entry nodes for a newly created run."""
        async with self.locked_run(run_id):
            await self.orchestrate_start_locked(run_id)

    async def orchestrate_start_locked(self, run_id: str) -> None:
        """``orchestrate_start`` body for callers already holding the
        run's lock.

        Use cases that need to perform a reset + start sequence
        atomically (e.g. ``TestFullWorkflowUseCase``) take the lock via
        ``locked_run(run_id)`` themselves and call this variant.
        """
        # Fetch BEFORE entering the scope — see ``_enter_run_scope``.
        run = await self._run_repo.aget_by_id(run_id)
        if not run:
            return
        tokens = self._enter_run_scope(run_id, run)
        try:
            await self._orchestrate_start_inner(run_id, run)
        finally:
            self._exit_run_scope(tokens)

    def run_lock(self, run_id: str) -> "asyncio.Lock":
        """Raw per-run mutex — the same underlying lock ``locked_run``
        wraps, exposed for callers that need an ``asyncio.Lock`` object
        (the orchestration worker's ``EditNodeDataAction`` guard checks
        ``isinstance(..., asyncio.Lock)``).

        Prefer ``locked_run(run_id)`` for new code: it registers the
        held run id in the per-task context, so nested run-scoped work
        (e.g. a child workflow completing synchronously inside the
        holder's section, whose ``complete_parent`` re-acquires the
        parent's lock) re-enters instead of deadlocking. A raw
        ``run_lock`` section must NOT call anything that can re-acquire
        the same run's lock — use the ``*_locked`` orchestrate variants
        inside it.
        """
        return self._get_run_lock(run_id)

    async def _orchestrate_start_inner(
        self, run_id: str, run: WorkflowRun | None = None,
    ) -> None:
        if run is None:
            run = await self._run_repo.aget_by_id(run_id)
        if not run:
            return

        # A start must never resurrect a run that has already reached a
        # terminal state. There was no guard here at all: a StartRunAction
        # arriving after a cancel — a redelivery, a slow queue, or simply a
        # cancel racing a start — would dispatch entry nodes onto a cancelled
        # run and bring it back to life.
        #
        # The status has to come from the AUTHORITATIVE reader, not
        # ``run.status``. Since Phase 3b.2c a cancel emits ``RunCancelled``
        # and deliberately does NOT write the aggregate's STATUS column, so
        # the row still reads CREATED or PENDING after a successful cancel.
        # A guard on ``run.status`` would look correct and never fire.
        from app.layer2_application.run_status_reader import (
            read_authoritative_status,
        )
        status = read_authoritative_status(run, self._view_store)
        if status in {
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        }:
            logging.getLogger(__name__).info(
                "Refusing to start run %s — it is already %s", run_id, status,
            )
            return

        workflow = self.load_workflow_for_run(run)
        if not workflow:
            return

        # No pre-run DAG validation: execution is fan-out driven — the run
        # starts and ``compute_fanout`` decides the next nodes as each one
        # completes. Structural validation is opt-in (``skip_validation`` on
        # StartRunUseCase, the /validate endpoint, dry-run), not a run gate.
        topology = run.context.topology
        try:
            # Phase 4 Sub 4.2 — under EXECUTOR_MODE=executor, the
            # ``RunStartConsumer`` is the authoritative driver of
            # entry-node dispatch. It subscribes to ``RunStarted``
            # (already emitted by the start_run use case before this
            # method runs) and emits ``NodeInput`` per entry node;
            # ``TaskCreatorConsumer`` (Sub 4.1) creates the Tasks and
            # the executor invokes the handlers. The engine's inline
            # ``handle_entry_nodes`` walk is skipped to avoid double
            # dispatch.
            if not self._is_executor_mode():
                task_factory = TaskFactory()
                await self.handle_entry_nodes(
                    topology.entry_nodes, run, workflow, topology, task_factory
                )
            await self._run_repo.asave(run)
            await self.drain_events()
            await self.check_run_completion(run, workflow)
            await self.drain_events()
        except Exception as exc:
            logging.getLogger(__name__).exception(
                "orchestrate_start failed: run=%s", run_id
            )
            new_error = str(exc)
            new_completed_at = datetime.now(timezone.utc).isoformat()
            run.status = RunStatus.FAILED
            run.error = new_error
            run.completed_at = new_completed_at

            def _apply_start_failed(r: WorkflowRun) -> None:
                r.status = RunStatus.FAILED
                r.error = new_error
                r.completed_at = new_completed_at

            await save_run_with_cas_retry(
                self._run_repo, run.id, _apply_start_failed,
            )
            self.emit_run_status(run, "failed", error=new_error)
            await self.drain_events()

    async def orchestrate_continue(
        self, run_id: str, task_id: str, test_mode: str | None = None,
    ) -> None:
        """Process successors after a task completes."""
        async with self.locked_run(run_id):
            # Fetch BEFORE entering the scope — see ``_enter_run_scope``.
            run = await self._run_repo.aget_by_id(run_id)
            if not run:
                return
            tokens = self._enter_run_scope(run_id, run)
            try:
                task = await self._task_repo.aget_by_id(task_id)
                if not task:
                    return
                workflow = self.load_workflow_for_run(run)
                if not workflow:
                    return

                await self.process_and_dispatch_successors(
                    task, run, workflow, run.context.topology, test_mode=test_mode,
                )
                # Loop-body advancement hook. A WORKER (async) body task
                # completes via the callback → CompleteTaskUseCase →
                # ContinueRunAction → HERE. Without this call the
                # event-driven loop never advanced past iteration 0 for a
                # worker/PENDING body (it only advanced for inline BUILTIN
                # bodies, which complete via orchestrate_execute_node where the
                # hook already fires — which is why the in-process integration
                # tests using TRIGGER bodies passed while real worker-body loops
                # stalled on tenant-1). maybe_enqueue gates on terminal status,
                # so it fires exactly once here (the dispatch-site call at
                # orchestrate_execute_node saw the body only RUNNING and no-op'd).
                await self.maybe_enqueue_loop_iteration(task, run)
                # Persist the iter_progress / iter_failed_nodes mutation
                # maybe_enqueue made — ONLY for a loop-body completion.
                # iteration_done decides purely from the PERSISTED iter_progress,
                # so on a fresh-deserialize repo (HANA) a multi-node worker body
                # would otherwise lose earlier body nodes' progress between
                # per-node completions and stall. Guarded so the ~all non-loop
                # completions don't pay an extra save on this hot path.
                _loop_scope = task.get_innermost_scope() if task else None
                if _loop_scope is not None and _loop_scope.invocation_id:
                    await self._run_repo.asave(run)
                await self.drain_events()
            finally:
                self._exit_run_scope(tokens)

    async def orchestrate_fail(self, run_id: str, task_id: str) -> None:
        """Route via failure edges or fail the run after a task fails."""
        async with self.locked_run(run_id):
            await self.orchestrate_fail_locked(run_id, task_id)

    async def orchestrate_fail_locked(self, run_id: str, task_id: str) -> None:
        """``orchestrate_fail`` body for callers already holding ``run_lock``.

        ``asyncio.Lock`` is not reentrant. The orchestration worker takes
        the per-run lock around ``EditNodeDataAction``, and the cancel-via-
        PATCH path inside ``EditNodeDataUseCase._cancel_pending_task`` needs
        to drive failure-edge routing inside that same critical section —
        calling ``orchestrate_fail`` from there would deadlock against the
        worker's lock. Use this variant whenever the caller already holds
        ``run_lock(run_id)``.
        """
        # Fetch BEFORE entering the scope — see ``_enter_run_scope``.
        run = await self._run_repo.aget_by_id(run_id)
        if not run:
            return
        tokens = self._enter_run_scope(run_id, run)
        try:
            task = await self._task_repo.aget_by_id(task_id)
            if not task:
                return
            workflow = self.load_workflow_for_run(run) if self._workflow_snapshot_repo else None
            topology = run.context.topology if run.context else None

            processing_result = None
            if workflow and topology:
                processing_result = await self.process_and_dispatch_successors(
                    task, run, workflow, topology,
                )
                # Track the failed task for loop iteration advancement
                # so the loop can emit LoopIterationCompleted(outcome="failed").
                await self.maybe_enqueue_loop_iteration(task, run)

            routed = bool(processing_result and processing_result.tasks_to_dispatch)
            if not routed and run.status not in {
                RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED,
            }:
                # No failure route (or no workflow/dag) — mark the run
                # FAILED so downstream callers observe a terminal state.
                if getattr(task, "max_retries", 0) > 0:
                    logging.getLogger(__name__).warning(
                        "Task %s (node %s) failed with max_retries=%d but retry logic is not implemented — "
                        "falling through to run failure",
                        task.id, task.node_id, task.max_retries,
                    )
                new_error = self._format_node_failure_error(task, workflow)
                new_completed_at = datetime.now(timezone.utc).isoformat()
                run.status = RunStatus.FAILED
                run.error = new_error
                run.completed_at = new_completed_at

                def _apply_task_failed(r: WorkflowRun) -> None:
                    r.status = RunStatus.FAILED
                    r.error = new_error
                    r.completed_at = new_completed_at

                await save_run_with_cas_retry(
                    self._run_repo, run.id, _apply_task_failed,
                )
                self.emit_run_status(
                    run, "failed", error=new_error, failed_task_id=task.id,
                )
                self.publish_callback(run, "FAILED")
                if run.parent_run_id and run.parent_node_id:
                    await self.fail_parent_tracking_task(run)

            await self.drain_events()
        finally:
            self._exit_run_scope(tokens)

    async def force_fail_run(self, run_id: str, error_message: str) -> None:
        """Last-resort terminal transition for a run whose orchestration
        action threw an unhandled exception.

        A thrown ``orchestrate_continue`` / ``orchestrate_resume`` /
        ``orchestrate_execute_node`` (e.g. a resolver, OData, or file-service
        error inside ``process_and_dispatch_successors``) otherwise leaves the
        just-completed task terminal but the run stuck ``RUNNING`` forever: the
        broker has already acked the action (no redelivery) and the in-process
        path wires no completion backstop. This forces the run to ``FAILED`` so
        callers observe a terminal state and a hung child fails its parent
        tracking task instead of hanging it too.

        Terminal-only and idempotent: it does NOT re-run successor processing
        (that is what threw) and no-ops when the run is already terminal. The
        orchestration worker invokes it from its exception handler, so it
        guards its own body and never propagates — the caller also wraps it.
        """
        async with self.locked_run(run_id):
            # Fetch BEFORE entering the scope — see ``_enter_run_scope``.
            run = await self._run_repo.aget_by_id(run_id)
            if run is None or run.status in {
                RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED,
            }:
                return
            tokens = self._enter_run_scope(run_id, run)
            try:
                new_completed_at = datetime.now(timezone.utc).isoformat()
                run.status = RunStatus.FAILED
                run.error = error_message
                run.completed_at = new_completed_at

                def _apply_force_failed(r: WorkflowRun) -> None:
                    r.status = RunStatus.FAILED
                    r.error = error_message
                    r.completed_at = new_completed_at

                await save_run_with_cas_retry(
                    self._run_repo, run.id, _apply_force_failed,
                )
                self.emit_run_status(run, "failed", error=error_message)
                self.publish_callback(run, "FAILED")
                if run.parent_run_id and run.parent_node_id:
                    await self.fail_parent_tracking_task(run)
                await self.drain_events()
            finally:
                self._exit_run_scope(tokens)

    async def orchestrate_resume(
        self, run_id: str, task_id: str, test_mode: str | None = None,
    ) -> None:
        """Process successors after a pending task is resumed.

        Identical to orchestrate_continue — the use case already completed
        the task and stored its output before enqueueing.
        """
        await self.orchestrate_continue(run_id, task_id, test_mode=test_mode)

    async def orchestrate_check_completion(self, run_id: str) -> None:
        """Re-evaluate run quiescence — completion-ONLY, no fan-out.

        The executor-mode counterpart to the completion tail of
        ``orchestrate_continue``. Under ``EXECUTOR_MODE=executor`` the
        engine bows out of handler invocation and the DispatcherConsumer
        owns fan-out, so this path must NOT call
        ``process_and_dispatch_successors`` (that would double the
        dispatcher's work). The ``RunCompletionConsumer`` enqueues a
        ``CheckRunCompletionAction`` on every ``NodeCompleted`` /
        ``NodeFailed``; ``check_run_completion`` is quiescence-gated and
        idempotent, so a check that fires before the run is done is a
        safe no-op.

        Race note: completing on an intermediate node before its
        successor task exists would prematurely finish the run. In the
        combined-pod topology the executor publishes through the
        in-process tee chain, so a node's ``NodeOutputProduced``
        synchronously drives Dispatcher → TaskCreator (successor tasked)
        BEFORE its ``NodeCompleted`` enqueues this check — that
        happens-before makes the check safe. When the consumers become
        broker subscribers (a true process split) that ordering no
        longer holds and this path must be revisited.
        """
        async with self.locked_run(run_id):
            # Fetch BEFORE entering the scope — see ``_enter_run_scope``.
            run = await self._run_repo.aget_by_id(run_id)
            if not run:
                return
            tokens = self._enter_run_scope(run_id, run)
            try:
                workflow = self.load_workflow_for_run(run)
                if not workflow:
                    return
                await self.check_run_completion(run, workflow)
                await self.drain_events()
            finally:
                self._exit_run_scope(tokens)

    async def orchestrate_resume_run(self, run_id: str) -> None:
        """Re-dispatch stalled tasks for a resumed run."""
        run = await self._run_repo.aget_by_id(run_id)
        if not run:
            return
        workflow = self.load_workflow_for_run(run)
        if not workflow:
            return
        all_tasks = await self._task_repo.alist_by_run(run_id)
        for task in all_tasks:
            if task.status.value == "READY":
                await self._enqueue_or_dispatch(task, run, workflow, run.context.topology)
        await self.drain_events()

    # ------------------------------------------------------------------
    # Unified queue-based node execution (non-recursive)
    # ------------------------------------------------------------------

    async def orchestrate_execute_node(self, run_id: str, task_id: str) -> None:
        """Execute a single node via the unified, non-recursive pipeline.

        Called by the ``OrchestrationWorker`` when an ``ExecuteNodeAction``
        is dequeued.  Instead of recursing into successor nodes, this method
        enqueues new ``ExecuteNodeAction`` messages for each successor.
        """
        async with self.locked_run(run_id):
            try:
                await self._execute_node_inner(run_id, task_id)
            except Exception as exc:
                logging.getLogger(__name__).exception(
                    "orchestrate_execute_node failed: run=%s task=%s", run_id, task_id,
                )
                # Delegate to the SAME terminal path the orchestration worker's
                # safety net uses. This block used to fail the run by hand: CAS
                # save + emit_run_status, and nothing else. It never called
                # ``publish_callback`` and never called
                # ``fail_parent_tracking_task`` — so a CHILD run that died here
                # went FAILED while its parent sat at the WORKFLOW node in
                # RUNNING, forever.
                #
                # Measured on tenant-1 2026-08-25, 6/6 runs of the sub-workflow
                # repro, and the child's error string was the tell:
                #
                #   bare "Run 'x': expected version N, found N+1"  -> this block
                #                                                  -> parent hung, 2/2
                #   "Orchestration failed while processing ..."    -> force_fail_run
                #                                                  -> parent FAILED, 3/3
                #
                # Nothing rescued the hang either: the child-timeout sweeper
                # enumerates RUNNING children only (an already-FAILED child is
                # invisible to it) and skips any WORKFLOW node without an
                # explicit positive ``timeout_ms``, which is the default.
                #
                # ``force_fail_run`` is terminal-only, idempotent, re-enters this
                # already-held lock, and guards its own body — so this is a
                # strict superset of what was here, with the two missing
                # notifications added.
                await self.force_fail_run(run_id, str(exc))

    async def _execute_node_inner(self, run_id: str, task_id: str) -> None:
        run = await self._run_repo.aget_by_id(run_id)
        if not run or run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return
        task = await self._task_repo.aget_by_id(task_id)
        if not task:
            return
        # Idempotency (at-least-once queue, no dedup key): a redelivered
        # ExecuteNodeAction (worker retry / broker redrive) must NOT re-run
        # _exec_uniform — re-firing a non-idempotent handler (DATA_EDIT
        # re-provisions its working copy / replays data-factory row ops)
        # double-applies the edit. A node that already paused (PENDING) or
        # reached a terminal status is never a legitimate fresh dispatch target.
        # Legitimate re-runs (loop bodies, cycle revisits) always get a fresh
        # -v{n} task id (see TaskFactory.task_id_for); rerun keeps the task id but
        # sets RUNNING, which this set excludes — see _NON_DISPATCHABLE_STATUSES.
        # ponytail: Path A (duplicate ContinueRunAction → spurious -v{n} revisit)
        # is a separate gap not closed here — it would surface as a second pending
        # prompt; fix at the successor-routing layer if it ever shows up.
        if task.status in _NON_DISPATCHABLE_STATUSES:
            logging.getLogger(__name__).info(
                "Skipping duplicate ExecuteNodeAction: run=%s task=%s status=%s",
                run_id, task_id, getattr(task.status, "value", task.status),
            )
            return
        workflow = self.load_workflow_for_run(run)
        if not workflow:
            return
        node = workflow.get_node(task.node_id)
        if not node:
            return

        topology = run.context.topology

        # Apply loop iteration variables from scope_stack.
        #
        # Dual-write: ``run.context.variables["loop"]`` keeps the
        # legacy contract — every handler resolves ``$loop.index`` /
        # ``$loop.item`` etc. against the run's flat dict, exactly as
        # before. In parallel, an ``IterationLocalsFrame`` is swapped
        # into the ambient ``ResolutionScope`` so the overlay path
        # (used when handlers ever migrate, or when concurrent
        # body-task scoping is needed) sees the same loop dict.
        # The dict-write race is fixed in the follow-up that drops
        # the dict-write once handlers read scope-only.
        loop_inner = task.get_innermost_scope()
        loop_iteration_locals: Mapping[str, Any] | None = None
        if loop_inner is not None:
            loop_items = run.context.loop_state.get(
                loop_inner.loop_node_id, {},
            ).get("items", [])
            iter_i = int(loop_inner.iter)
            loop_iteration_dict = {
                "index": iter_i,
                # SA-1977 — ``items`` is EITHER a concrete list OR a file_ref
                # pointer (SA-1905 D2.4 keeps a large collection lazy so the CP
                # never holds 24k rows in RAM). This line used to index it
                # directly. On a pointer, ``len(dict)`` is the KEY COUNT, so the
                # bounds guard passed — and ``pointer[0]`` raised ``KeyError: 0``,
                # which surfaced to the user as the run error string "0", because
                # ``str(KeyError(0))`` IS "0".
                #
                # The driver owns reading an item out of a collection (one row via
                # OData for a pointer, plain indexing for a list). Ask it. The
                # engine has no business knowing which shape it is holding — that
                # knowledge is exactly what it kept getting wrong.
                "item": await self._loop_item_at(loop_items, iter_i),
                "total": int(loop_inner.total),
                "first": iter_i == 0,
                "last": iter_i == int(loop_inner.total) - 1,
                "depth": 0,
            }
            run.context.variables["loop"] = loop_iteration_dict
            loop_iteration_locals = {"loop": loop_iteration_dict}

        # Edge-based dispatch: the predecessor-readiness AND-join guard has
        # been retired. A node is dispatched the moment one of its incoming
        # edges is taken (or, for MERGE, the handler tracks quorum itself
        # via ``merge_quorum_buffers`` and returns ``deferred`` until the
        # configured strategy fires).

        # Per-iteration scope swap. When the run's ambient scope is set
        # (engine wired with a scope_builder), we install an
        # ``IterationLocalsFrame`` carrying this body task's loop
        # iteration state so the overlay path resolves ``$loop.index``
        # consistently with the legacy dict-write above. The swap is
        # contained to this single ``_exec_uniform`` call — concurrent
        # body tasks each get their own scope token and don't share an
        # overlay frame.
        ambient_scope = get_current_scope() if loop_iteration_locals else None
        iter_token: Any = None
        if ambient_scope is not None and loop_iteration_locals is not None:
            iter_scope = ambient_scope.with_replaced_frame(
                IterationLocalsFrame,
                IterationLocalsFrame(locals_=loop_iteration_locals),
            )
            iter_token = set_current_scope(iter_scope)
        try:
            await self._exec_uniform(task, node, run, workflow, topology)
        finally:
            if iter_token is not None:
                reset_current_scope(iter_token)

        await self._run_repo.asave(run)
        await self.drain_events()
        await self.check_run_completion(run, workflow)
        await self.drain_events()

    # ------------------------------------------------------------------
    # Phase 6 — Uniform execution model
    # ------------------------------------------------------------------

    async def _exec_uniform(
        self, task: Task, node: Any, run: WorkflowRun,
        workflow: Workflow, topology: Any,
    ) -> None:
        """Uniform execution path for ALL node types.

        Flow:
        1. Emit ``NodeInput`` at the dispatch boundary + hydrate the
           run's variables from the projector view.
        2. Invoke the node handler (same for every type).
        3. Apply loop-state updates and dispatch to the matching
           per-mode runner — see ``node_execution_runner.py``. The six
           modes (``immediate`` / ``dispatched`` / ``pending`` /
           ``child_run`` / ``loop_body`` / ``deferred``) used to live
           as elif branches here; they were extracted because each is
           a coherent execution shape that benefits from being read in
           isolation.
        4. Drain buffered events, publish handler events, and check
           whether the loop iteration is done.
        """

        now = datetime.now(timezone.utc).isoformat()

        # 1a. Emit NodeInput at the dispatch boundary.
        # Phase 0 of engine split: today this is observational because the
        # Executor + Dispatcher share a process. Phase 2 splits them, at
        # which point this event becomes the message the Dispatcher sends
        # to the Executor. Emitting now keeps the event stream
        # self-sufficient (a Projector replaying from the log sees every
        # input handoff).
        iteration_index = (
            task.scope_stack[-1].iter if task.scope_stack else None
        )
        self._publish_now(
            EventTopic.NODE_INPUT,
            NodeInput(
                event_id=EventId.derive(task.id, f"input:{node.id}"),
                run_id=run.id,
                workflow_id=run.workflow_id,
                node_id=node.id,
                task_id=task.id,
                # Wire-thin: drop ``extracted`` from file_ref values before
                # the event leaves the engine. Executors / projectors
                # reconstruct inputs from the pointer via materialise.
                # Also redact __credentials — never published in plaintext.
                resolved_input={
                    k: v for k, v in (task.resolved_input or {}).items()
                    if k != "__credentials"
                },
                iteration_index=iteration_index,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
            ),
        )

        # 1b. Hydrate ``run.context.variables`` with projector view
        # entries so handler input resolution sees completed predecessor
        # outputs. Phase 3b.1 moved node outputs from the aggregate to
        # the projector; handlers still read ``run.context.variables``
        # directly when calling ``resolver.resolve_mapping``, so without
        # this hydration paths like ``{{$<predecessor_node_id>.__file_id}}``
        # raise PATH_NOT_FOUND with ``Available sources: ['artifacts',
        # 'input']``. ``setdefault`` keeps aggregate-owned keys
        # (``input`` / ``artifacts``) authoritative — we only fill in
        # missing per-node entries from the view.
        if self._view_store is not None:
            try:
                view = self._view_store.get(run.id)
            except Exception:
                view = None
            if view is not None:
                ctx_vars = run.context.variables
                for vk, vv in view.variables.items():
                    ctx_vars.setdefault(vk, vv)

        # Phase 3 Gate 3 — under executor mode the engine publishes
        # NodeInput and the standalone Executor pod owns the rest of
        # the ``_exec_uniform`` flow (state updates, edge routing,
        # successor dispatch) via NodeOutputProduced + Dispatcher +
        # projector. Engine returns here.
        if self._is_executor_mode():
            iter_idx = task.scope_stack[-1].iter if task.scope_stack else None
            self._emit_node_input_for_executor(
                node=node, run=run, workflow=workflow, task=task,
                iteration_index=iter_idx,
            )
            return

        # Activate the run ResolutionScope so handlers resolve
        # ``$variables`` / ``$variable`` / ``$parent`` / ``$root``. Built
        # HERE — after the view→aggregate hydration above — so the
        # ``SystemVariablesFrame`` captures the freshly-hydrated ``__vars``
        # file_ref: ``apply_state_updates_as_files`` pops ``__vars`` from
        # the aggregate at publish time (view is authoritative), so a scope
        # built at the ``orchestrate_execute_node`` entry would see an empty
        # ``vars_view`` and ``{{$variables.<name>}}`` in a CONDITION / SWITCH
        # / COMPUTE expression would silently fail to resolve.
        #
        # Only entered when no ambient scope is already active. The
        # ``orchestrate_execute_node`` path (every uniform node dequeued
        # from an ``ExecuteNodeAction``) is the one entry point that does
        # NOT set the scope at its boundary — every sibling orchestrate_*
        # does — so this fills that gap. Entry-node / start paths already
        # hold a scope (and entry nodes have no upstream SET_VARIABLE, so
        # no ``__vars`` dependency) and are left untouched. ``$loop`` keeps
        # resolving via the legacy dict-write in ``_execute_node_inner``,
        # not this frame. Executor-authoritative mode already returned
        # above — the standalone executor pod owns its own scope wiring.
        exec_scope_tokens = None
        if get_current_scope() is None:
            try:
                exec_scope_tokens = self._enter_run_scope(run.id, run)
            except Exception:
                logging.getLogger(__name__).debug(
                    "exec-scope activation failed; degrading to no-scope",
                    exc_info=True,
                )
                exec_scope_tokens = None
        try:
            # 2. Invoke handler. The per-mode runners below each emit
            # ``node.dispatched`` once they know the mode, so consumers
            # never see a data-less or mode-ambiguous dispatch.
            handler = self._handler_registry.get(node.node_type)
            try:
                handler_result = await invoke_handler(
                    handler,
                    monitor=self._monitor,
                    node=node, run=run, workflow=workflow, topology=topology,
                    completed_task=None, task_factory=self._task_factory,
                    current_scope_stack=task.scope_stack,
                )
            except (
                ExpressionError, InputRequirementError, LoopCollectionTypeError,
            ) as exc:
                # SA-1977 — a LOOP pointed at a non-collection is an AUTHORING
                # error, exactly like an unresolvable expression, so it must land
                # the same way: NodeDispatched + NodeFailed, node red on the
                # canvas with the message. Escaping to the run-level catch-all
                # instead failed the RUN while leaving the LOOP node READY and
                # non-terminal — a change whose whole purpose is to POINT AT the
                # mistake must not leave the mistake unmarked.
                # Preserve the universal contract: every task that reaches
                # handler invocation must emit NodeDispatched before its
                # terminal NodeFailed, even when input resolution fails.
                #
                # LIFECYCLE-ONLY. The handler raised, so this task has no
                # resolved input — ``task_handler`` sets ``started_at`` and
                # attaches ``resolved_input`` only AFTER the point that threw.
                # Without this flag the event defaults to dispatch_to_worker=
                # True and every transport forwards the empty task to a worker,
                # which then fails on the missing input and overwrites this
                # node's error with its own. That is tenant-1 run c9ef6ca1:
                # the message humans read said "'issue_key' is required" while
                # the real fault was a different field's expression entirely.
                # The event still reaches the canvas; only the worker hop is cut.
                self.emit_node_dispatched(task, run, node, dispatch_to_worker=False)
                await self._mark_task_failed(task, node, run, str(exc))
                # Still track the failed task for loop iteration advancement
                # so the loop can detect "iteration done" even when a body
                # task fails, and emit ``LoopIterationCompleted(outcome="failed")``.
                await self.maybe_enqueue_loop_iteration(task, run)
                return

            # 3a. Apply loop-state updates (in-memory) — must happen for
            # all modes since subsequent branches read from it. Publishing
            # ``state_updates`` via ``apply_state_updates_as_files`` is now
            # per-mode (Step 1 of the PENDING-leak fix): only modes whose
            # handle-time output is the final node output (``immediate``)
            # publish here. Other modes (``pending``, ``child_run``,
            # ``loop_body``, ``dispatched``) defer publication to the path
            # that actually marks node completion — otherwise
            # ``DispatcherConsumer`` (subscribed to ``NodeOutputProduced``)
            # would synchronously emit ``EdgeTraversed`` for outgoing edges
            # before the node has actually completed.
            if handler_result.loop_state_updates:
                run.context.loop_state.update(handler_result.loop_state_updates)

            # 3b. Dispatch to the per-mode runner.
            await self._exec_runner.run(
                handler_result.completion_mode,
                ExecutionContext(
                    task=task, node=node, run=run, workflow=workflow,
                    topology=topology, handler_result=handler_result, now=now,
                ),
            )

            # 4. Drain buffered events first (e.g. the NODE_DISPATCHED a
            # mode runner emitted for the WORKFLOW / LOOP node itself) so
            # they reach the wire before the handler's tail events instead
            # of being overtaken by them.
            await self.drain_events()
            self._publish_now_many(handler_result.events)

            # 5. Loop body tracking hook (Phase 5.2.2).
            await self.maybe_enqueue_loop_iteration(task, run)
        finally:
            if exec_scope_tokens is not None:
                self._exit_run_scope(exec_scope_tokens)

    async def _route_to_successors(
        self,
        node: Any,
        handler_result: NodeHandlerResult,
        task: Task,
        run: WorkflowRun,
        workflow: Workflow,
        topology: Any,
    ) -> None:
        """Phase 6 — pure edge routing based on handler's output port.

        Given a completed node and its handler result, follow outgoing
        edges filtered by the handler's port selection and dispatch
        each successor via ``_enqueue_or_dispatch``.

        Three routing modes:
        1. Handler selected specific edges (``taken_edge_ports`` non-empty —
           CONDITION/SWITCH that matched a wired branch, gated PARALLEL,
           resumed HUMAN_ACTION): follow ONLY those edges.
        2. A branching node (CONDITION/SWITCH) selected NO edges: dispatch
           nothing. This is the bug #3 guard — an unwired chosen branch must
           NOT fall through to mode 3, where ``compute_fanout`` (which does
           not port-route CONDITION/SWITCH) would traverse ALL outgoing edges
           and fire the wrong, wired branch.
        3. All other nodes (incl. branchless PARALLEL, which must fan out to
           all edges): ``output_port`` + ``compute_fanout`` with
           ``expected_port_override`` to filter edges by port.
        """
        taken_ports = handler_result.taken_edge_ports
        outgoing = topology.edge_map.get(node.id, [])

        # Propagate the parent task's scope_stack to successor tasks
        # so loop body tasks carry the enclosing LoopScope through
        # the successor chain — required by maybe_enqueue_loop_iteration.
        parent_scope = task.scope_stack

        if taken_ports:
            # The handler selected specific edges — follow ONLY those.
            # Covers CONDITION/SWITCH (wired branch matched), gated PARALLEL
            # (the condition-met branch subset) and resumed HUMAN_ACTION.
            # Phase 2a — read through view store; ``node.id`` is the just-
            # completed branching node, union into completed for the same
            # reason as ``process_successors`` (projector may not have
            # processed NodeCompleted yet in this call frame).
            view_completed = self._view_completed(run, extra=frozenset([node.id]))
            for edge in outgoing:
                if edge.id not in taken_ports:
                    continue
                port_id = taken_ports[edge.id]
                target_id = edge.target_node_id
                target_node = workflow.get_node(target_id)
                if target_node is None:
                    continue
                active = await self._active_task(run.id, target_id, target_node.node_type)
                if active is not None:
                    self._maybe_queue_revisit(run, target_id, active, parent_scope)
                    continue
                # SA-846: a taken edge into a completed node re-runs it
                # (including a self-loop — the source just completed, so
                # routing back to itself is a revisit), and a taken edge
                # into a skipped node un-skips it (all skips are
                # flow-derived, so a later taken edge overrides them).
                # Bump the visit counter BEFORE create_task so the
                # re-run gets a fresh ``-v{n}`` task id (and thereby
                # fresh task-id-derived event ids that survive log
                # dedupe). The un-skip keys on the AGGREGATE set so a
                # multi-arrival node bumps once per revisit.
                revisit = target_id in view_completed
                if target_id in run.context.skipped_nodes:
                    run.context.skipped_nodes.discard(target_id)
                    revisit = True
                if revisit:
                    visits = run.context.node_visits
                    visits[target_id] = visits.get(target_id, 0) + 1
                    discard_stale_node_variable(run, target_node)
                # Phase 3a PR 2: inline EdgeTraversed emission removed.
                # DispatcherConsumer (wired in bootstrap) publishes
                # EdgeTraversed authoritatively from the NodeOutputProduced
                # tee. Engine continues to own task creation + enqueue.
                new_task = self._task_factory.create_task(target_node, run)
                if parent_scope:
                    new_task.scope_stack = list(parent_scope)
                await self._task_repo.asave(new_task)
                await self._enqueue_or_dispatch(new_task, run, workflow, topology)
        elif node.node_type in BRANCHING_NODE_TYPES:
            # Bug #3: a CONDITION/SWITCH whose chosen branch is UNWIRED selects
            # NO edges. Dispatch nothing — do NOT fall through to the
            # ``compute_fanout`` else below, which (CONDITION/SWITCH aren't in
            # ``_PORT_ROUTED_TYPES``) would traverse ALL outgoing edges and
            # fire the wrong, wired branch.
            #
            # Restricted to CONDITION/SWITCH on purpose: a *branchless*
            # PARALLEL also leaves ``taken_edge_ports`` empty but MUST fan out
            # to every edge, so it belongs in the compute_fanout else — only
            # branching nodes have "selected nothing → route nothing" semantics.
            #
            # ``pass`` (not ``return``) so this branch falls through to the end
            # of the method exactly like the ``if``/``else`` branches do — if
            # trailing logic is ever added after the dispatch block, the
            # empty-selection case must not uniquely skip it.
            pass
        else:
            # All other nodes: port-based routing via compute_fanout.
            output_port = handler_result.output_port
            port_override = output_port if output_port != "out_default" else None
            decision = FanoutDecision.compute(
                causation_id=task.id,
                source_node_id=node.id,
                source_node_type=node.node_type,
                source_task_status=task.status,
                outgoing_edges=outgoing,
                expected_port_override=port_override,
            )
            # Edge-based dispatch: ``decision.skipped`` is always empty now;
            # the cascade-skip emission block has been retired alongside the
            # downstream-skip BFS. EdgeTraversed continues to be emitted by
            # the DispatcherConsumer from NodeOutputProduced.
            fanout_completed = self._view_completed(run, extra=frozenset([node.id]))
            for action in decision.actions:
                target_node = workflow.get_node(action.target_node_id)
                if target_node is None:
                    continue
                active = await self._active_task(run.id, target_node.id, target_node.node_type)
                if active is not None:
                    self._maybe_queue_revisit(run, target_node.id, active, parent_scope)
                    continue
                # SA-846: a taken edge into a completed node re-runs it,
                # and a taken edge into a skipped node un-skips it (all
                # skips are flow-derived) — bump the visit counter before
                # create_task so the re-run gets a fresh ``-v{n}`` task id.
                revisit = target_node.id in fanout_completed
                if target_node.id in run.context.skipped_nodes:
                    run.context.skipped_nodes.discard(target_node.id)
                    revisit = True
                if revisit:
                    visits = run.context.node_visits
                    visits[target_node.id] = visits.get(target_node.id, 0) + 1
                    discard_stale_node_variable(run, target_node)
                new_task = self._task_factory.create_task(target_node, run)
                if parent_scope:
                    new_task.scope_stack = list(parent_scope)
                await self._task_repo.asave(new_task)
                await self._enqueue_or_dispatch(new_task, run, workflow, topology)

    # _exec_builtin_queued REMOVED in Phase 6.3 — replaced by _exec_uniform.
    # _exec_pending_queued REMOVED in Phase 6.3 — replaced by _exec_uniform.
    # _exec_task_queued REMOVED in Phase 6.3 — replaced by _exec_uniform.


    async def orchestrate_loop_iteration(
        self, run_id: str, task_id: str,
        loop_node_id: str, iteration_index: int,
    ) -> None:
        """Feed a completed loop body back to the loop handler. Delegates
        to :class:`LoopIterationDriver`. Public name kept because
        the ``OrchestrationWorker`` and ``QueueRunner`` bind to it.
        """
        await self._loop_driver.orchestrate_iteration(
            run_id, task_id, loop_node_id, iteration_index,
        )
    def _build_output_data(
        self,
        run: WorkflowRun,
        workflow: Workflow,
    ) -> dict[str, Any]:
        """Build output_data from the authoritative run state, excluding aliases.

        Node outputs are read from the projector view, which is authoritative
        since Phase 3b.1 — ``NodeOutputProduced`` updates only the view, not the
        aggregate. Per key the view wins; ``run.context.variables`` is the
        fallback when the view has no entry (or no view store is wired, e.g.
        tests that build a ``WorkflowRun`` directly). Only canonical keys —
        node IDs plus ``input`` — are emitted; display-name / ``trigger``
        aliases are excluded to avoid duplication.

        Sourcing from the view fixes a divergence where ``run.output_data``
        (and the terminal ``run.status=completed`` event) carried a pending
        node's stale dispatch-time file_ref — e.g. an INPUT node's empty
        provisioned CSV — instead of its submitted data, while GET /data and
        GET /context (both view-backed) correctly showed the submitted output.

        OUTPUT-node contract: when the workflow defines OUTPUT node(s) and at
        least one of them produced an entry, only those entries are returned —
        the OUTPUT schema is the workflow's output interface. With no OUTPUT
        node (or every OUTPUT node skipped, e.g. on an untaken branch), all
        canonical entries are returned as before.
        """
        node_ids = {n.id for n in workflow.nodes}
        canonical_keys = node_ids | {"input"}

        view_vars: Mapping[str, Any] = {}
        if self._view_store is not None:
            try:
                view = self._view_store.get(run.id)
            except Exception:
                view = None
            if view is not None:
                # ``getattr`` guard: some view stubs / partial views expose
                # completed/skipped sets but not ``variables`` — fall back to
                # the aggregate rather than raising.
                view_vars = getattr(view, "variables", None) or {}

        agg_vars = run.context.variables or {}
        result: dict[str, Any] = {}
        for k in canonical_keys:
            if k in view_vars:
                result[k] = view_vars[k]
            elif k in agg_vars:
                result[k] = agg_vars[k]

        # OUTPUT contract: OUTPUT node entries (when present) ARE the
        # workflow's output — drop everything else, including ``input``.
        output_ids = {n.id for n in workflow.nodes if n.node_type == NodeType.OUTPUT}
        present = {k: result[k] for k in output_ids if k in result}
        if present:
            return present
        return result

    async def _loop_item_at(self, items: Any, index: int) -> Any:
        """The loop item at ``index`` — delegated to the loop driver, which is the
        one component that knows how to read a collection that may be a concrete
        list OR a lazy file_ref pointer (SA-1977).

        Falls back to plain indexing only when no driver is wired (unit tests
        constructing a bare engine), and even then never indexes a non-list — a
        pointer yields ``None``, not ``KeyError: 0``.

        **Deliberately NOT memoized**, and the reason is the whole point of this
        Story. A ``(file_id, index)`` cache looks obviously safe — uploads are
        content-addressed, so two runs sharing a ``file_id`` share identical
        bytes. But **identical bytes are not an identical value**: the bytes are
        decoded through ``column_types`` / ``value_kind`` / ``item_kind``, and
        those live on the **ref**, not in the file. One file, three values::

            b'__row_id,a\\r\\n1,1\\r\\n'          # ONE file_id (content dedupe)
              value_kind=object, a:number  ->  {'a': 1}
              value_kind=array,  a:number  ->  [{'a': 1}]
              value_kind=array,  a:string  ->  [{'a': '1'}]

        A node returning ``[{"id":"1"}]`` and a node returning ``[{"id":1}]`` write
        byte-identical CSV, dedupe to one ``file_id``, and such a cache hands the
        second loop the FIRST one's item. Keying a *decoded* value by ``file_id``
        alone is exactly the inference this module exists to delete — and it would
        have been the first thing in the system to do it (the resolver's own
        file-id cache is bucketed per ``root_run_id`` and dropped on run end).

        Cost, stated rather than optimised away: every task in a loop body carries
        the iteration's scope, so a K-node body over N iterations does K×N
        single-row OData reads under ``locked_run``, K-1 of every K redundant. That
        is a real cost on a path that, today, **crashes** — so it is not a
        regression, and it is not worth a cache that can serve another run's data.
        The right fix is to carry the item on the dispatched task (the driver has
        already fetched it), not to cache it globally; that touches the persisted
        scope and the event payload, so it is a follow-up, not a smuggled-in extra.
        """
        driver = self._event_driven_loop_driver
        if driver is not None:
            return await driver.item_at(items, index)
        if isinstance(items, (list, tuple)):
            return items[index] if 0 <= index < len(items) else None
        return None

    async def _to_file_ref(self, key: str, data: Any) -> Any:
        """Wrap *data* as a CSV file reference via the data store service."""
        if self._data_store_service is None:
            return data
        if not isinstance(data, (dict, list)):
            return data
        if is_file_ref(data):
            return data
        return await self._data_store_service.store_data(key, data)

    @staticmethod
    def apply_state_updates(
        state_updates: dict[str, Any],
        run: WorkflowRun,
        workflow: Workflow,
    ) -> None:
        """Apply handler state_updates to run context variables.

        Stores each entry under its original key (node ID) **and** under the
        node's name when the key matches a workflow node.  This allows
        expressions to reference nodes by name (e.g. ``{{$compute_subtotal.output.result}}``)
        in addition to by UUID.
        """
        for key, value in state_updates.items():
            run.context.variables[key] = value
            node = workflow.get_node(key)
            if node and node.name and node.name != key:
                run.context.variables[node.name] = value

    def _thin_for_the_wire(self, wrapped: dict[str, Any]) -> dict[str, Any]:
        """Under ``SCHEMA_REF_MODE=on``, drop the schema from refs on the EVENT.

        ``columns`` + ``column_types`` are 90.4% of every descriptor's bytes
        (§17.8) and the ``schema_hash`` beside them names the same pair in
        ``AIW_SCHEMA_DEF``. The view store puts them back before any reader sees
        ``variables`` (§19.3), so this is the only place the saving is taken.

        Two things this deliberately does NOT do:

        * It does not touch ``wrapped``. The mirror below still writes FULL refs
          to ``AIW_RUN_NODE_OUTPUT`` — the row is where the schema is looked up
          FROM, so thinning it would leave nothing to expand from.
        * It does not run under ``shadow``. Shadow observes; a mode that also
          changed the wire would make the two indistinguishable.

        The dictionary entries are written first, before the event is
        published, because a thin ref that reaches a reader ahead of its schema
        does not fail — it makes ``_unflatten`` guess the types.
        """
        try:
            from app.layer2_application.data import schema_dictionary as _sd

            if not _sd.enforcing():
                return wrapped
            repo = getattr(self, "_node_output_repo", None)
            thin: dict[str, Any] = {}
            for key, value in wrapped.items():
                if not is_file_ref(value):
                    thin[key] = value
                    continue
                if repo is not None:
                    # Ordering, not an optimisation — see ``ensure_schemas``.
                    repo.ensure_schemas(value)
                thin[key] = _sd.strip(value)
            return thin
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "schema thinning skipped, publishing full refs: %s", exc,
            )
            return wrapped

    def _record_node_output(
        self,
        wrapped: dict[str, Any],
        run: WorkflowRun,
        workflow: Workflow,
        *,
        task: Any = None,
    ) -> None:
        """Mirror every file-backed value in *wrapped* into AIW_RUN_NODE_OUTPUT.

        Also fills ``AIW_SCHEMA_DEF`` — ``HanaNodeOutputRepository.save`` writes
        the schema dictionary entry for each distinct ``schema_hash`` it stores.
        That is the whole point of moving this here: a hash present on the wire
        but absent from the dictionary cannot be expanded back into
        ``columns``/``column_types``, and the reader's fallback is
        ``_try_parse_value`` — it GUESSES the types rather than failing. 51 of
        88 hashes were in that state, all of them from paths that skip
        CompleteTaskUseCase.

        Inline values (``code`` / ``approval_flow`` under the size ceiling) are
        skipped, not failed: there is no file for a descriptor to point at, and
        ``FILE_ID`` is ``NOT NULL`` by design.

        **Never raises.** The authoritative write — the ``NodeOutputProduced``
        event — has already happened by the time this runs. A broken mirror must
        cost a row, never a run.
        """
        # ``getattr``, not attribute access: engines built with ``__new__`` (the
        # topology-rebuild tests, and any future partial construction) have no
        # such attribute, and a bare read would raise BEFORE the try below —
        # turning a mirror that must never fail a run into one that always can.
        repo = getattr(self, "_node_output_repo", None)
        if repo is None or not wrapped:
            return

        # Parity with CompleteTaskUseCase's stamps, exactly: a task carries the
        # generation it was created under (the G-guard), its loop iteration and
        # its retry count, and those three plus RUN_ID/NODE_ID are the primary
        # key. Deriving generation from ``run`` when a task IS present would
        # write a different key than the worker path does for the same node.
        if task is not None:
            generation = int(getattr(task, "run_generation", 0) or 0)
            iteration = int(getattr(task, "iteration_index", 0) or 0)
            attempt = int(getattr(task, "retry_count", 0) or 0)
        else:
            generation = int(getattr(run, "generation", 0) or 0)
            iteration = attempt = 0

        for node_id, value in wrapped.items():
            if not is_file_ref(value):
                continue
            node = workflow.get_node(node_id) if workflow is not None else None
            try:
                repo.save(
                    run_id=run.id,
                    node_id=node_id,
                    node_name=(getattr(node, "name", "") or ""),
                    file_ref=value,
                    generation=generation,
                    iteration=iteration,
                    attempt=attempt,
                )
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "node-output row not written for run=%s node=%s: %s",
                    run.id, node_id, exc,
                )

    async def apply_state_updates_as_files(
        self,
        state_updates: dict[str, Any],
        run: WorkflowRun,
        workflow: Workflow,
        *,
        source_node_id: str = "",
        causation_id: str = "",
        output_port: str = "",
        taken_edge_ports: dict[str, str] | None = None,
        attempt_id: str = "",
        task: Any = None,
    ) -> None:
        """Apply state_updates, storing each value as a CSV file reference.

        Phase 5.1: in addition to mutating ``run.context.variables``, emit a
        ``NodeOutputProduced`` event carrying the full write-set (including
        name aliases). This lets the Projector rebuild ``variables`` from
        the event log alone.

        Phase 0 of engine split: ``NodeOutputProduced`` is emitted BEFORE
        ``apply_state_updates`` mutates ``run.context.variables``. This
        makes the event stream authoritative — a Projector replaying from
        the log sees the write-set before any downstream consumer could
        observe the mutated state. Reversing the order would let a
        Projector fall behind without a corresponding event to catch up on.

        Callers SHOULD pass ``source_node_id`` and ``causation_id`` so the
        emitted event carries provenance. If neither is provided the event
        still fires with empty provenance — the projector rebuild still
        works, but Phase 5.2's event-sourced dispatcher will want stable
        causation for redelivery dedupe.
        """
        if not state_updates:
            return

        # I/O: wrap each value as a CSV file reference via the data store.
        wrapped: dict[str, Any] = {}
        for key, value in state_updates.items():
            node = workflow.get_node(key)
            snapshot = (getattr(node, "definition_snapshot", None) or {}) if node else {}
            if (
                node is not None
                and not is_file_ref(value)
                and (
                    should_keep_inline(getattr(node, "worker_type", None), value)
                    or should_keep_inline(snapshot.get("base_worker_id"), value)
                )
            ):
                # Inline-output nodes (code) keep their raw dict so downstream
                # expressions resolve the real values, not a file_id pointer.
                # Must mirror complete_task's inline decision, else the value
                # gets re-file-backed here.
                #
                # SA-1951 — that mirror now includes the SIZE ceiling
                # (``should_keep_inline``, not ``keeps_inline_output``). An
                # oversized inline value would otherwise ride the
                # NodeOutputProduced event straight into a broker that rejects it
                # at ~1 MB. Above the ceiling we fall through to ``_to_file_ref``
                # like any other worker.
                wrapped[key] = value
            else:
                wrapped[key] = await self._to_file_ref(key, value)

        # PURE: build the full write-set (primary keys + name aliases) and the
        # ``NodeOutputProduced`` event that carries it. The projector replays
        # this event to rebuild ``variables``. See
        # ``WorkflowRun.plan_state_updates``.
        event = run.plan_state_updates(
            self._thin_for_the_wire(wrapped),
            workflow,
            source_node_id=source_node_id,
            causation_id=causation_id,
            output_port=output_port,
            taken_edge_ports=taken_edge_ports,
            attempt_id=attempt_id,
        )
        # Phase 3b.1: the projector rebuilds ``variables`` from
        # ``NodeOutputProduced`` and the view store is the sole authoritative
        # read path. Emitting the event is the only persistent effect — the
        # aggregate write was removed.
        self._publish_now(EventTopic.NODE_OUTPUT_PRODUCED, event)

        # Mirror each file-backed value into AIW_RUN_NODE_OUTPUT (+ the schema
        # into AIW_SCHEMA_DEF). HERE, because this is the one place every node
        # output passes through; the previous writer sat on CompleteTaskUseCase
        # and so missed every builtin.
        #
        # AFTER the publish, deliberately: the event is the authoritative write,
        # the row is a derived index of it. Mirroring first would let the table
        # lead the event stream — ahead-of-truth, which is worse than behind.
        self._record_node_output(wrapped, run, workflow, task=task)

        # SA-846 revisit staleness: invalidate the aggregate copies of the
        # keys just published. The view→aggregate hydration (``_exec_uniform``
        # and the pending re-request path) uses ``setdefault``, so an
        # aggregate entry hydrated on a PREVIOUS visit would shadow the
        # fresh view value forever — a loop CONDITION around a re-run node
        # kept resolving iteration 1's output and spun the cycle endlessly.
        # The in-process projector tee has already folded the event into the
        # view by the time ``_publish_now`` returns, so the next hydration
        # refills these keys with the new value. View-less harnesses (no
        # view store wired) keep their pre-seeded aggregate entries —
        # nothing would refill them. ``getattr``: phase-0 contract tests
        # probe this method on a bare ``__new__`` engine instance.
        if getattr(self, "_view_store", None) is not None:
            for key in event.state_updates:
                run.context.variables.pop(key, None)

        # Read-side mirror for the Output panel. The aggregate ``variables``
        # write was removed in Phase 3b.1 (projector is authoritative), but
        # ``last_output_ports`` / ``last_taken_edge_ports`` are pure
        # additive scalars that ``GetNodeDataUseCase`` reads directly off
        # ``run.context``. Projector reducer populates the same fields on
        # replay, so live and rebuilt state stay in sync.
        if event.node_id and output_port:
            run.context.last_output_ports[event.node_id] = output_port
        if event.node_id and taken_edge_ports:
            run.context.last_taken_edge_ports[event.node_id] = dict(taken_edge_ports)

    # ------------------------------------------------------------------
    # Child workflow lifecycle — delegated to ChildWorkflowCoordinator.
    # The five methods below are thin wrappers around ``self._child_coord``;
    # see ``child_workflow_coordinator.py`` for the implementation.
    # Engine retains the names because the internal call sites (``_exec_uniform``
    # child_run branch, ``check_run_completion``, ``orchestrate_fail``) plus
    # the public ``IOrchestrationEngine`` interface bind to them.
    # ------------------------------------------------------------------

    async def _start_child_workflow(
        self,
        node: Any,
        parent_task: Task,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        handler_result: Any,
    ) -> None:
        await self._child_coord.start(
            node, parent_task, run, workflow, topology, handler_result,
        )

    async def _check_ancestry_cycle(
        self, run: WorkflowRun, child_workflow_id: str,
    ) -> None:
        await self._child_coord._check_ancestry_cycle(run, child_workflow_id)

    async def _complete_parent_tracking_task(
        self, child_run: WorkflowRun,
    ) -> None:
        await self._child_coord.complete_parent(child_run)

    async def fail_parent_tracking_task(self, child_run: WorkflowRun) -> None:
        """Public entry — also part of ``IOrchestrationEngine`` and called
        from ``sweep_timed_out_child_runs_use_case``."""
        await self._child_coord.fail_parent(child_run)

    async def _find_tracking_task(
        self, run_id: str, node_id: str,
    ) -> Task | None:
        return await self._child_coord._find_tracking_task(run_id, node_id)
