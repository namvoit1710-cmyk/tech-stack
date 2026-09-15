"""Per-mode runners for ``OrchestrationEngine._exec_uniform``.

The engine invokes a node handler and the handler returns a
``NodeHandlerResult`` whose ``completion_mode`` field tells the engine
how to react. There are six modes (see ``NodeHandlerResult``):

* ``immediate`` — BUILTIN handler finished the node in-process; mark
  COMPLETED, publish dispatched/completed, route to successors.
* ``dispatched`` — TASK handler resolved input; ship it to a worker
  and wait for the callback (``CompleteTaskUseCase`` / ``FailTaskUseCase``).
* ``pending`` — PENDING handler set up the form / file slot; save the
  PENDING task and wait for the user submit.
* ``child_run`` — WORKFLOW handler prepared child metadata; spawn the
  child run via :class:`ChildWorkflowCoordinator`.
* ``loop_body`` — LOOP handler queued body tasks; enqueue them and
  announce LOOP-body fan-out.
* ``deferred`` — handler did partial work (e.g. MERGE quorum not met);
  no routing, no dispatched event.
* ``absorbed`` — late fan-in arrival at an already-fired MERGE quorum;
  the task terminates quietly (COMPLETED, no routing, no events) so the
  straggler neither re-runs the downstream subtree nor stalls quiescence.

Each mode used to live as an ``elif`` branch inside one ~300-line
method on the engine. They moved out so each branch can be reasoned
about, edited, and reviewed in isolation.

The runner is coupled to the engine via ``self._engine`` for
orchestration callbacks (publishing events, dispatching successors,
starting child workflows, etc.). The coupling is intentional — the
runner doesn't try to be testable in isolation; the engine's
integration tests cover all six modes (the 4-layer nested-workflow
test alone exercises ``immediate``, ``pending``, ``child_run``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from collections.abc import Awaitable, Callable

from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.exceptions import ExpressionError
from app.layer1_domain.events import (
    EventTopic,
    LoopBodyDispatched,
    NodeCompleted,
)
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.node_spec import get_spec
from app.layer1_domain.node_spec.model import NodeRuntime
from app.layer2_application.interfaces.orchestration_queue_interface import (
    ExecuteNodeAction,
)


# Claim-check a task's resolved input to a file only when it is large enough to
# threaten the dispatch message-size limit. Below this, the input rides the
# dispatch INLINE with full fidelity.
#
# Why the gate matters: the claim-check stores the input via ``store_data``,
# which (with RECURSIVE_ARTIFACT_SPLIT on) splits every array-of-object field —
# an HTTP node's ``headers``, or a ``body`` bound to a nested payload like
# ``{{$input.payload}}`` — into separate artifact CSVs. But the pointer it then
# emits is only ``{file_id, data_mode: "csv_single"}`` (see below): it carries
# NO ``artifacts``, and the worker's file-ref resolver fetches just that one
# scalar file. So a claim-checked input SILENTLY loses its array fields — the
# worker receives no ``body`` and the request goes out empty (``resolved_body``
# null). Keeping small inputs inline avoids the lossy round-trip entirely for the
# common case; a genuinely large array-valued input still offloads and needs the
# deeper worker-side artifact re-join (tracked separately), so that path logs a
# warning rather than dropping silently.
_TASK_INPUT_CLAIM_CHECK_BYTES = 256 * 1024


def _approx_input_bytes(value: Any) -> int:
    """Best-effort serialized size of a resolved input (0 if it can't serialize)."""
    try:
        return len(json.dumps(value, default=str).encode("utf-8"))
    except Exception:
        return 0


def _engine_inline_builtin_type(worker_type: str | None) -> NodeType | None:
    """Return the builtin ``NodeType`` a ``worker_type`` names IFF that type's
    spec declares ``runtime="engine"`` — i.e. it is handled INLINE by the
    control plane and must never be dispatched to a worker. Otherwise ``None``.

    This is the fail-fast signal for a mis-typed node (e.g. ``node_type=TASK``
    carrying ``worker_type="builtin:workflow"``): such a node reaches the
    worker-dispatch path, but no worker serves a ``builtin:*`` type, so without
    this it would sit RUNNING forever with no error.

    Keyed off the node-spec ``runtime`` — the single source of truth the engine
    uses for its inline / worker classification — NOT the literal ``builtin:``
    prefix. If a builtin ever migrates to executor/worker execution, its spec
    ``runtime`` flips away from ``"engine"`` and this guard stops firing for it
    automatically, with no change here.
    """
    if not worker_type or not worker_type.startswith("builtin:"):
        return None
    try:
        node_type = NodeType(worker_type[len("builtin:"):].upper())
    except ValueError:
        return None
    return node_type if get_spec(node_type).runtime == NodeRuntime.ENGINE else None


@dataclass
class ExecutionContext:
    """Frozen-ish bag of args every mode runner needs.

    Built once in the engine's ``_exec_uniform`` preamble and passed to
    the matching runner method.
    """
    task: Task
    node: Any
    run: WorkflowRun
    workflow: Workflow
    topology: Any
    handler_result: NodeHandlerResult
    now: str  # ISO-8601 timestamp captured at the start of execution


class NodeExecutionRunner:
    """Dispatches a node handler's ``completion_mode`` to the matching
    per-mode runner. Each runner mutates ``ctx.task`` / ``ctx.run`` and
    drives the engine's downstream publishing + routing as needed.
    """

    def __init__(self, *, engine: Any) -> None:
        self._engine = engine

    async def run(self, mode: str, ctx: ExecutionContext) -> None:
        """Look up the runner for ``mode`` and execute it."""
        runner = self._DISPATCH.get(mode)
        if runner is None:
            # Unknown / unhandled mode → no-op (same shape as
            # ``deferred`` — handler did something internal, nothing
            # for the engine to do).
            return
        await runner(self, ctx)

    # ------------------------------------------------------------------
    # immediate — BUILTIN handler finished in-process
    # ------------------------------------------------------------------

    async def _run_immediate(self, ctx: ExecutionContext) -> None:
        eng = self._engine
        task, node, run = ctx.task, ctx.node, ctx.run
        handler_result = ctx.handler_result

        # Mark completed in the run's in-memory aggregate.
        for skipped_id in handler_result.skipped_node_ids:
            run.context.skipped_nodes.add(skipped_id)
        run.context.completed_nodes.add(node.id)
        task.status = TaskStatus.COMPLETED
        task.completed_at = ctx.now
        eng._task_repo.save(task)

        # Universal event-driven contract:
        #   NodeInput → NodeDispatched → NodeOutputProduced
        #             → EdgeTraversed → NodeCompleted
        # Publish directly (not buffered) so the order on the wire
        # matches the dispatched-mode flow in CompleteTaskUseCase.
        # ``input={}`` — immediate-mode handlers compute everything
        # internally, no resolved input to expose in the event.
        eng.emit_node_dispatched(task, run, node, input={})
        await eng.apply_state_updates_as_files(
            handler_result.state_updates, run, ctx.workflow,
            source_node_id=node.id,
            causation_id=task.id,
            output_port=handler_result.output_port or "",
            taken_edge_ports=handler_result.taken_edge_ports,
            attempt_id=task.idempotency_key or "",
            # BUILTIN handlers never reach CompleteTaskUseCase, which is why
            # 152 of 420 completed tasks had no AIW_RUN_NODE_OUTPUT row and
            # their schemas never entered AIW_SCHEMA_DEF. Passing the task here
            # is what closes that gap — the mirror lives in the helper now.
            task=task,
        )
        # Publish NodeCompleted BEFORE dispatching successors so the
        # node's own terminal event stamps a lower per-run sequence
        # than any successor's NodeDispatched / NodeCompleted. Without
        # this, ``_route_to_successors`` recursively executes the
        # entire downstream subtree before NodeCompleted gets published,
        # giving it a seq much higher than its descendants and breaking
        # frontend node-status rendering (completed appears to land
        # "after" downstream events). Order on the wire matches the
        # contract: NodeDispatched → NodeOutputProduced → EdgeTraversed
        # → NodeCompleted.
        eng._publish_now(
            EventTopic.NODE_COMPLETED,
            NodeCompleted(
                task_id=task.id, run_id=run.id, workflow_id=run.workflow_id,
                node_id=node.id,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level, source="workflow-control-plane",
                iteration_index=(
                    task.scope_stack[-1].iter if task.scope_stack else None
                ),
                # CONDITION / SWITCH carry branch / expression /
                # condition_result here (folded from ConditionEvaluated).
                **handler_result.completion_payload,
            ),
        )
        # Announce gated-off branches (e.g. a PARALLEL branch whose condition
        # was false) as skipped so the projector view and FE canvas agree with
        # the aggregate — otherwise the node appears stuck PENDING forever and
        # the view diverges from ``run.context.skipped_nodes`` (set above). The
        # projector folds ``NodeCancelled(reason="branch_not_taken")`` into
        # ``skipped_nodes``; deterministic event_id dedupes on replay.
        for skipped_id in handler_result.skipped_node_ids:
            eng.emit_node_cancelled(
                run, node_id=skipped_id, reason="branch_not_taken",
            )
        await eng._route_to_successors(
            node, handler_result, task, run, ctx.workflow, ctx.topology,
        )

    # ------------------------------------------------------------------
    # dispatched — TASK handler ships to a worker
    # ------------------------------------------------------------------

    async def _run_dispatched(self, ctx: ExecutionContext) -> None:
        eng = self._engine
        task, node, run = ctx.task, ctx.node, ctx.run
        handler_result = ctx.handler_result

        # Fail-fast: a node reached the worker-dispatch path but its
        # worker_type names an ENGINE-inline builtin (e.g. a WORKFLOW node
        # mis-typed as node_type=TASK + worker_type="builtin:workflow"). No
        # worker serves a builtin:* type — the dispatchers drop it — so the
        # task would otherwise sit RUNNING forever with no error. Fail it now
        # with an actionable message. Spec-runtime keyed, so a future
        # executor-served builtin (runtime != engine) is NOT affected.
        misrouted_worker_type = handler_result.worker_type or node.worker_type
        misrouted = _engine_inline_builtin_type(misrouted_worker_type)
        if misrouted is not None:
            await eng._mark_task_failed(
                task, node, run,
                f"Node '{node.id}' has node_type={node.node_type.value} with "
                f"worker_type='{misrouted_worker_type}', but "
                f"'{misrouted_worker_type}' is an engine-inline builtin handled "
                f"by the control plane, not a dispatchable worker. The node is "
                f"mis-typed — set node_type={misrouted.value}.",
                error_code="MisroutedInlineNode",
            )
            return

        if handler_result.resolved_input is not None:
            task.resolved_input = handler_result.resolved_input
        if handler_result.worker_type:
            task.worker_type = handler_result.worker_type

        # Apply CSV data mode and file storage (mirrors the legacy
        # ``_exec_task_queued`` path).
        await eng._apply_csv_data_mode(task, node)
        if (
            eng._data_store_service
            and task.resolved_input
            and "file_id" not in task.resolved_input
            and _approx_input_bytes(task.resolved_input) > _TASK_INPUT_CLAIM_CHECK_BYTES
        ):
            try:
                file_ref = await eng._data_store_service.store_data(
                    f"{task.id}-input", task.resolved_input,
                )
                if is_file_ref(file_ref):
                    # SA-2072 — emit the FULL file_ref (artifacts + value_kind +
                    # column_types), NOT a lossy ``{file_id, data_mode:"csv_single"}``
                    # pointer. ``store_data`` splits every array-of-object field — an
                    # HTTP node's ``headers``, or a ``body`` bound to a nested payload
                    # like ``{{$input.payload}}`` — into ``nested_array`` /
                    # ``array_field`` child artifacts. The old pointer carried NONE of
                    # them, so the worker fetched one scalar CSV and silently lost those
                    # fields (empty request body → SAP /convert 422). The full ref is
                    # small (pointers + schema metadata, no data) so it still rides the
                    # dispatch inline; an SA-2072-aware worker reconstructs the object
                    # from the artifact tree (parity with ``DataStoreService.materialize``).
                    #
                    # BACKWARD COMPAT (worker-sdk is a shared library; a worker only
                    # rebuilds when its OWN dir changes, so CP and workers deploy out of
                    # lockstep): keep a ``data_mode:"csv_single"`` fallback KEY on the
                    # ref. An OLD (not-yet-rebuilt) worker still sees ``is_file_id_input``
                    # → reads the scalar CSV → the SAME behaviour as before this change
                    # (the pre-existing bug, NOT a new regression). A NEW worker checks
                    # ``__file_ref`` + artifacts FIRST and reconstructs, ignoring the
                    # fallback. So CP can ship ahead of the workers with no big-bang.
                    task.resolved_input = {**file_ref, "data_mode": "csv_single"}
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Failed to store task input as file for task %s: %s",
                    task.id, exc,
                )

        task.status = TaskStatus.RUNNING
        task.started_at = ctx.now
        eng._task_repo.save(task)
        eng._stage_recorder.record(
            run_id=run.id, node_id=task.node_id, task_id=task.id,
            event_type=EventTopic.NODE_DISPATCHED,
            from_status=TaskStatus.READY.value,
            to_status=TaskStatus.RUNNING.value,
            metadata={"worker_type": task.worker_type or ""},
        )
        # Default ``input`` on the dispatched event carries the
        # worker's resolved input — this is the message the worker
        # actually consumes.
        eng.emit_node_dispatched(task, run, node)

    # ------------------------------------------------------------------
    # pending — PENDING handler paused for user
    # ------------------------------------------------------------------

    async def _run_pending(self, ctx: ExecutionContext) -> None:
        eng = self._engine
        node, run = ctx.node, ctx.run
        handler_result = ctx.handler_result

        pending = handler_result.pending_task
        if pending is None and handler_result.tasks_to_dispatch:
            # Fallback: handler used the legacy ``tasks_to_dispatch``
            # field instead of the typed ``pending_task`` slot.
            pending = handler_result.tasks_to_dispatch[0]
        if pending is None:
            return
        eng._task_repo.save(pending)
        # ``input={}`` — the user-facing form payload is provided via
        # the secondary InputRequested-shaped event, not here.
        eng.emit_node_dispatched(pending, run, node, input={})
        eng._stage_recorder.record(
            run_id=run.id, node_id=pending.node_id,
            task_id=pending.id, event_type="task.pending_input",
            from_status=TaskStatus.READY.value,
            to_status=TaskStatus.PENDING.value,
            metadata={"node_type": node.node_type.value},
        )
        # Mirror the freshly-provisioned working ref into the projector view
        # so ``GET /data?side=output`` surfaces it at dispatch (copy-on-write
        # with the input side, per the node-data API contract) instead of an
        # empty slot until the first PATCH / submit.
        #
        # We deliberately do NOT publish the port-bearing ``NodeOutputProduced``
        # that ``apply_state_updates_as_files`` emits — ``DispatcherConsumer``
        # would fan it out into ``EdgeTraversed`` / ``NodeInput`` for
        # successors, but the node hasn't completed and the form isn't
        # submitted. ``emit_variables_seeded`` rides the ``__bootstrap__``
        # source the dispatcher skips, so the write-set lands in the view
        # fan-out-free. The ``seed_id`` carries the loop-iteration index so a
        # PENDING node re-dispatched per iteration publishes a distinct event
        # instead of deduping against the first iteration's provisioned ref.
        if handler_result.state_updates:
            iteration_index = (
                pending.scope_stack[-1].iter if pending.scope_stack else None
            )
            seed_id = (
                f"node_output:{node.id}:{iteration_index}"
                if iteration_index is not None
                else f"node_output:{node.id}"
            )
            eng.emit_variables_seeded(
                run, dict(handler_result.state_updates), seed_id=seed_id,
            )

    # ------------------------------------------------------------------
    # child_run — WORKFLOW handler spawns a child workflow
    # ------------------------------------------------------------------

    async def _run_child_run(self, ctx: ExecutionContext) -> None:
        eng = self._engine
        task, node, run = ctx.task, ctx.node, ctx.run
        handler_result = ctx.handler_result

        # Emit dispatched for the WORKFLOW tracking task itself; the
        # child run's entry nodes will emit their own when they execute.
        # Buffered so it drains alongside the child's seed events in
        # priority order.
        eng.emit_node_dispatched(task, run, node, input={}, buffer=True)
        for child_task in handler_result.tasks_to_dispatch:
            child_task.status = TaskStatus.RUNNING
            child_task.started_at = ctx.now
            eng._task_repo.save(child_task)
        try:
            await eng._start_child_workflow(
                node, task, run, ctx.workflow, ctx.topology, handler_result,
            )
        except ExpressionError as exc:
            # An authoring/config fault the child start diagnosed before the
            # child run existed. It lands on THIS node, red, with that message.
            #
            # The credential refusal that originally motivated this guard no
            # longer arrives here: since SA-2287 (B3) a sub-workflow's secrets
            # are fetched by the node inside the child that needs them, so the
            # refusal lands on THAT node instead — a strictly better place. The
            # guard stays because it is not credential-specific and the escape
            # route it closes is real for any other ExpressionError the child
            # start raises.
            #
            # Without this it escapes ``_exec_uniform`` (whose only handler
            # catch wraps ``invoke_handler``, not the mode runners) to the
            # run-level catch-all, which fails the whole RUN and leaves the
            # WORKFLOW node non-terminal — the same shape SA-1977 fixed for
            # LOOP. ``NodeDispatched`` was already emitted above, so the
            # dispatched-before-failed contract holds.
            await eng._mark_task_failed(task, node, run, str(exc))
            await eng.maybe_enqueue_loop_iteration(task, run)

    # ------------------------------------------------------------------
    # loop_body — LOOP handler queued body tasks
    # ------------------------------------------------------------------

    async def _run_loop_body(self, ctx: ExecutionContext) -> None:
        eng = self._engine
        task, node, run = ctx.task, ctx.node, ctx.run
        handler_result = ctx.handler_result

        # Emit dispatched for the LOOP node itself; body tasks re-enter
        # ``_exec_uniform`` and emit their own dispatched events.
        # Buffered so it drains alongside the body-task seed events.
        eng.emit_node_dispatched(task, run, node, input={}, buffer=True)

        view_completed = eng._view_completed(run)
        view_skipped = eng._view_skipped(run)
        dispatched_body_ids: list[str] = []
        first_iter_index: int | None = None
        for body_task in handler_result.tasks_to_dispatch:
            if (
                body_task.node_id in view_completed
                or body_task.node_id in view_skipped
            ):
                continue
            eng._task_repo.save(body_task)
            dispatched_body_ids.append(body_task.node_id)
            if first_iter_index is None and body_task.scope_stack:
                first_iter_index = body_task.scope_stack[-1].iter
            await eng._queue.enqueue(
                ExecuteNodeAction(run_id=run.id, task_id=body_task.id)
            )

        # Phase 3a.3 — announce loop body fan-out. The
        # DispatcherConsumer emits EdgeTraversed for each LOOP → body
        # edge; engine stays out of EdgeTraversed emission.
        if dispatched_body_ids:
            eng._publish_now(
                EventTopic.LOOP_BODY_DISPATCHED,
                LoopBodyDispatched(
                    run_id=run.id,
                    workflow_id=run.workflow_id,
                    loop_node_id=node.id,
                    body_node_ids=list(dispatched_body_ids),
                    iteration_index=first_iter_index,
                    causation_id=task.id,
                    root_run_id=run.root_run_id or run.id,
                    depth=run.depth_level,
                    source="workflow-control-plane",
                ),
            )

    # ------------------------------------------------------------------
    # deferred — MERGE quorum not met / handler intentionally pauses
    # ------------------------------------------------------------------

    async def _run_deferred(self, ctx: ExecutionContext) -> None:
        # Handler did partial work but the node isn't done. No status
        # change, no routing, no dispatched event. The handler typically
        # has already saved its intermediate state to the run context.
        return

    # ------------------------------------------------------------------
    # absorbed — straggler arrival at an already-fired MERGE quorum
    # ------------------------------------------------------------------

    async def _run_absorbed(self, ctx: ExecutionContext) -> None:
        # The arrival is already recorded in the quorum buffer; the node's
        # status and output remain those of the firing visit. Terminate the
        # revisit task quietly: COMPLETED so run quiescence isn't blocked
        # (a "deferred" straggler task would sit READY forever), but no
        # routing and no state/event publication — downstream must fire at
        # most once per quorum.
        ctx.task.status = TaskStatus.COMPLETED
        ctx.task.completed_at = ctx.now
        self._engine._task_repo.save(ctx.task)

    # ------------------------------------------------------------------
    # Mode → runner table
    # ------------------------------------------------------------------

    _DISPATCH: dict[str, Callable[["NodeExecutionRunner", ExecutionContext], Awaitable[None]]] = {
        "immediate": _run_immediate,
        "dispatched": _run_dispatched,
        "pending": _run_pending,
        "child_run": _run_child_run,
        "loop_body": _run_loop_body,
        "deferred": _run_deferred,
        "absorbed": _run_absorbed,
    }
