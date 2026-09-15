"""Event-driven loop driver — the Phase 5.2.1 replacement for the
imperative ``_run_loop_iterations`` walker.

Responsibilities
================

1. **Absorb** the loop lifecycle logic that previously lived in
   ``LoopNodeHandler._ed_initialize_loop`` / ``_ed_iteration_complete``
   / ``_dispatch_iteration_body`` / ``_ed_emit_loop_completed_and_exit``.
   This is a single-source-of-truth move: the handler becomes a thin
   delegation shim and the driver owns the real logic.

2. **Add** a new ``run_loop`` entry point that the engine calls
   directly when ``EVENT_DRIVEN_LOOP`` is on. Unlike the legacy
   walker, ``run_loop`` publishes ``LoopStarted`` + the initial
   ``LoopIterationStarted`` **before** dispatching the first body
   task, so the event log's topological order matches the logical
   sequence. The legacy walker published those events only after
   the walker returned, which meant the first per-iteration body
   events appeared in the log before ``LoopStarted`` — a latent
   ordering quirk that Phase 4c.4.3's integration test worked
   around with set-based assertions. Phase 5.2.1 fixes it.

3. **Preserve** the handler-interface-compatible methods
   (``initialize_loop``, ``iteration_complete``) so the Phase 4c.4.3
   handler tests continue passing through a delegation shim without
   rewriting them. These methods have the same signatures and
   semantics as the old handler methods.

Scope of this slice (Phase 5.2.1)
=================================

- **Inline-only.** The driver uses ``enqueue_or_dispatch`` injected
  from the engine to run body tasks via recursive inline dispatch,
  the same way the old walker did. Queued-mode support is Phase
  5.2.2 scope (requires wiring ``LoopIterationAction`` dead code).

- **Loops only.** The driver handles LOOP nodes and their bodies.
  It does NOT touch CONDITION, SWITCH, MERGE, or any other BUILTIN
  flow. Those remain with the recursive engine path until later
  Phase 5 slices migrate them.

- **Feature-gated.** The engine instantiates and routes to the
  driver only when ``settings.EVENT_DRIVEN_LOOP`` is true. Flag
  OFF = byte-identical legacy behavior (``LoopNodeHandler`` +
  ``_run_loop_iterations`` walker unchanged).

Interface with the engine
=========================

The driver takes no engine reference in its constructor — instead,
the engine passes two callables to ``run_loop``:

- ``enqueue_or_dispatch(task, run, workflow, topology) -> Awaitable``
  — dispatches a task inline (equivalent to
  ``OrchestrationEngine._enqueue_or_dispatch``). The driver uses
  this to run body iterations.

- ``publish_events(events: list[DomainEvent]) -> None`` — publishes
  a list of domain events to the event publisher chain (equivalent
  to ``OrchestrationEngine._publish_now_many``). The driver calls
  this to stream events in real-time as iterations progress.

This inversion keeps the driver pure-domain: it doesn't depend on
the engine, only on two typed callables, so it can be unit-tested
in isolation without spinning up the whole orchestration stack.
"""

from __future__ import annotations

import logging
import json
import os
from typing import Any
from collections.abc import Awaitable, Callable, Mapping

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.node_spec.specs import LoopConfig
from app.layer1_domain.entities.data_reference import (
    is_file_ref,
    build_file_ref,
    add_artifact,
)
from app.layer1_domain.events import (
    DomainEvent,
    LoopCompleted,
    LoopIterationCompleted,
    LoopIterationStarted,
    LoopStarted,
)
from app.layer1_domain.exceptions import LoopCollectionTypeError
from app.layer1_domain.value_objects.event_id import EventId
from app.layer1_domain.value_objects.value_kind import (
    ARRAY,
    NULL,
    OBJECT,
    UNKNOWN,
    kind_of,
    kind_of_inline,
)
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    VariableResolutionError,
    VariableResolver,
)
from app.layer1_domain.value_objects.loop_scope import (
    LoopScope,
    derive_invocation_id,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus


def _strict_collection() -> bool:
    """Does a LOOP over a non-collection FAIL, or silently run zero times?

    SA-1977's acceptance is *"a LOOP over an object fails fast with a typed error
    (never 0 silent iterations)"*, so this defaults **ON**. But it is a real
    behaviour change on a deployed product, and the honest thing is to say how far
    it reaches: **the legacy-ref fallback protects nothing here.** A live run
    re-executes its upstream node, which writes a FRESH ref declaring
    ``value_kind: "object"`` — so a workflow that today loops zero times over an
    object (say an author pointing a LOOP at ``{{$httpNode}}`` instead of
    ``{{$httpNode.data}}``) goes red on its very next run after deploy. There is no
    soak period and no telemetry on how many workflows do this.

    So: one env var, no code deploy, to put it back. ``LOOP_STRICT_COLLECTION=false``
    restores the old zero-iteration behaviour — but logs it loudly rather than
    silently, which is the part that was never acceptable.

    Read per call (not at import) so an operator's ``cf set-env`` + restart takes
    effect, and so tests can flip it.
    """
    return os.getenv("LOOP_STRICT_COLLECTION", "true").strip().lower() not in (
        "false", "0", "off", "no",
    )


# Callable types used by ``run_loop`` for engine-injected side effects.
EnqueueOrDispatch = Callable[
    [Task, WorkflowRun, Workflow, GraphTopology], Awaitable[None]
]


# The pure body-subgraph helpers (``is_iteration_done``,
# ``iterate_body_subgraph`` and friends) live in
# ``app.layer1_domain.loop_topology``; ``WorkflowRun.decide_loop_iteration``
# wraps ``is_iteration_done``. Import them from layer1 directly.
PublishEvents = Callable[[list[DomainEvent]], None]


# SA-1905 F2 — default max iteration-result entries held in RAM before a full
# window is spilled to a durable chunk file_ref. Sized so one window fits the
# <=10 MB/step budget for typical iteration outputs; overridable per driver.
_ITERATION_RESULTS_WINDOW = 1000

# The SIZE ceiling on the in-RAM iteration-results window.
#
# ``_ITERATION_RESULTS_WINDOW`` alone counts ENTRIES, and that is the wrong
# axis. Measured on prod-br10 / tenant-1: ``iteration_results`` is 65% of every
# byte of ``loop_state`` ever stored (2.61 MB of 4.01 MB across four loops), yet
# **every LoopStarted event ever written to that tenant — all 23 of them — has
# total <= 100**. So the 1000-entry gate could not fire even once, while single
# entries measured in the hundreds of KB. Spilling was ON
# (``WORKER_CHUNKED_OUTPUT_ENABLED=true``) the whole time and did nothing.
#
# This is the same defect as ``keeps_inline_output`` ignoring SIZE (SA-1949 /
# SA-1951), one layer up: a threshold on count guarding a problem of bytes.
#
# 256 KB matches ``INLINE_OUTPUT_MAX_BYTES`` — the point at which this codebase
# already decided a value is too big to ride an event.
_ITERATION_RESULTS_MAX_BYTES = int(
    os.getenv("LOOP_ITERATION_RESULTS_MAX_BYTES", str(256 * 1024))
)

# The SIZE ceiling on an INLINE ``items`` collection.
#
# ``items`` is 35% of every byte of ``loop_state`` ever stored on tenant-1 — the
# second half of the payload, after ``iteration_results``. Three of the four
# measured loops hold it as a concrete list; only one is already a file_ref.
#
# The number is derived from the column, not invented. ``loop_state`` now has a
# durable home in ``AIW_RUN_LOOP_STATE.STATE_JSON NVARCHAR(5000)``, of which the
# scalars measure ~107 B, so ~4 KB is what an inline collection can occupy and
# still fit. Above it the collection has to become a pointer.
#
# NOT "always a ref": ``item_at`` reads a file_ref one row at a time through
# OData, so claim-checking a 4-item loop would trade zero HTTP calls for four on
# the loop hot path. ``total`` is p50 **4** and max 100 across all 23 loops ever
# run here, so the common case must stay inline. The threshold is the whole
# point — a blanket rule is wrong in one direction or the other.
_LOOP_ITEMS_MAX_INLINE_BYTES = int(
    os.getenv("LOOP_ITEMS_MAX_INLINE_BYTES", str(4 * 1024))
)

# A ``count`` / ``condition`` loop's items are ``range(n)`` — the index IS the
# value. Materialising that list put up to 10,000 integers (~48 KB) into
# ``loop_state`` for a workload that never reads them: a condition loop iterates
# until its expression goes false and touches ``items`` only for the count.
#
# So the range is carried as this marker instead. ``_items_total`` and
# ``item_at`` are the only two readers of ``items`` in the system (the engine's
# ``_loop_item_at`` delegates to the latter), which is what keeps a third shape
# to five lines rather than an audit.
#
# It also settles a knot the claim-check created: at the default
# ``max_iterations`` a materialised range is past ``_LOOP_ITEMS_MAX_INLINE_BYTES``,
# so it would upload a 10,000-row CSV at loop start and then pay an OData fetch
# per iteration to read back a number equal to the index it was asked for.
RANGE_COUNT_KEY = "__index_range__"


def is_index_range(items: Any) -> bool:
    """Is *items* the synthetic ``range(n)`` marker rather than real data?"""
    return isinstance(items, dict) and RANGE_COUNT_KEY in items


def index_range(count: int) -> dict[str, int]:
    return {RANGE_COUNT_KEY: int(count)}


# Artifact type stamped on each spilled iteration-results chunk. materialize
# concatenates all chunks of this type back into the full results list.
LOOP_CHUNK_ARTIFACT = "loop_chunk"


class EventDrivenLoopDriver:
    """Owns the full loop iteration orchestration flow for the
    event-driven path.

    Three public entry points:

    - ``initialize_loop`` — handler-interface-compatible; returns a
      ``NodeHandlerResult`` with init events + first body task to
      dispatch. Used by ``LoopNodeHandler._ed_initialize_loop``
      delegation shim so existing Phase 4c.4.3 unit tests keep
      passing.

    - ``iteration_complete`` — handler-interface-compatible; returns
      a ``NodeHandlerResult`` advancing or terminating the loop. Used
      by ``LoopNodeHandler._ed_iteration_complete`` delegation shim.

    - ``run_loop`` — engine-facing; drives the FULL iteration
      sequence inline, publishing events in logical order. The
      engine calls this directly when wired via
      ``OrchestrationEngine.__init__(event_driven_loop_driver=...)``.
      ``run_loop`` is strictly more powerful than the legacy walker
      + handler combination because it owns event publication
      timing and doesn't rely on the walker's synthetic-task feedback
      dance.
    """

    def __init__(
        self,
        variable_resolver: VariableResolver,
        run_context_view_store: object | None = None,
        data_store_service: Any = None,
        iteration_results_window: int = _ITERATION_RESULTS_WINDOW,
        iteration_results_max_bytes: int = _ITERATION_RESULTS_MAX_BYTES,
        items_max_inline_bytes: int = _LOOP_ITEMS_MAX_INLINE_BYTES,
    ) -> None:
        self._resolver = variable_resolver
        self._view_store = run_context_view_store
        # Materialize authority for file-backed collections. A loop is a
        # whole-value consumer: when ``{{$node.items}}`` resolves to a
        # file-backed array_field reference (a pointer the resolver keeps
        # opaque so *worker* inputs don't download it), the loop must turn
        # it into a real list here. Optional so the many test call sites
        # that construct ``EventDrivenLoopDriver(resolver)`` keep working.
        self._store = data_store_service
        # SA-1905 F2 — bound in-RAM ``iteration_results`` to this window; when
        # spilling is enabled, a full window is flushed to a durable chunk
        # file_ref and cleared, so a million-iteration loop never holds all
        # results in RAM (nor serializes them into loop_state).
        self._results_window = iteration_results_window
        # The size half of the same gate — see _ITERATION_RESULTS_MAX_BYTES.
        self._results_max_bytes = iteration_results_max_bytes
        # The equivalent ceiling for ``items`` — see _LOOP_ITEMS_MAX_INLINE_BYTES.
        self._items_max_inline_bytes = items_max_inline_bytes

    def _spill_enabled(self) -> bool:
        """Spill iteration results only when a store is wired AND the chunked
        output feature is on — otherwise behaviour is byte-identical (results
        accumulate inline as before)."""
        return self._store is not None and bool(
            getattr(self._store, "_chunked_enabled", False)
        )

    def _variables_view(self, run: WorkflowRun) -> Mapping[str, Any]:
        """Phase 2a — return the view-store-backed variables mapping
        when available, falling back to the aggregate for tests that
        wire the driver without a view store. Engine writes still go to
        the aggregate until Phase 3b.

        Returns a flat dict on purpose: ``VariableResolver._resolve_single``
        accepts a dict and consults the ambient ``ResolutionScope``
        (set by the engine before each ``orchestrate_*`` pass) for any
        source the dict doesn't hold — so ``$parent`` / ``$root`` /
        ``$variable`` resolve correctly inside loop condition and
        ``items`` expressions without this method needing to know
        about the scope object.
        """
        if self._view_store is None:
            return run.context.variables
        view = self._view_store.get(run.id)
        if view is None:
            return run.context.variables
        return view.variables

    # ───────────────────── handler-interface methods ─────────────────────

    async def initialize_loop(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        completed_task: Task | None,
        task_factory: TaskFactory,
    ) -> NodeHandlerResult:
        """Initialise a new loop invocation: resolve items, derive
        invocation_id, build loop_state, emit ``LoopStarted``, and
        dispatch the first body task(s).

        Returns a ``NodeHandlerResult`` with ``tasks_to_dispatch``
        populated for the walker to pick up. Signature and semantics
        match the old ``LoopNodeHandler._ed_initialize_loop``.
        """
        result = NodeHandlerResult()
        items, total, loop_type, mode, parallelism, condition_expr, counter_variable = (
            await self._resolve_loop_config(node, run)
        )

        # Derive enclosing scopes from the completed task (if the loop
        # is itself inside an outer loop). Empty for a top-level loop.
        enclosing: list[LoopScope] = []
        if completed_task is not None and completed_task.scope_stack:
            enclosing = list(completed_task.scope_stack)

        invocation_id = derive_invocation_id(run.id, node.id, enclosing)
        enclosing_payload = [s.to_dict() for s in enclosing]

        items = await self._claim_check_items(items, invocation_id)

        loop_state = {
            "items": items,
            "current_index": 0,
            "total": total,
            "completed_iterations": [],
            "failed_iterations": [],
            "mode": mode,
            "loop_type": loop_type,
            "condition": condition_expr,
            "counter_variable": counter_variable,
            "next_dispatch_index": 0,
            # Stored on loop_state so ``iteration_complete`` can
            # reconstruct scopes without re-deriving from the
            # synthetic task (which lacks ``scope_stack``).
            "invocation_id": invocation_id,
            "enclosing_scope_stack": enclosing_payload,
            "parallelism": parallelism,
            # Accumulate each iteration's body output so the loop's
            # final output contains the full collection of results.
            "iteration_results": [],
        }
        result.loop_state_updates = {node.id: loop_state}
        result.state_updates[node.id] = {
            "output": {},
            "metadata": {
                "type": "loop",
                "total": total,
                "mode": mode,
                "loop_type": loop_type,
            },
        }

        # Always emit LoopStarted, even for an empty collection, so
        # the event log reflects the loop was entered.
        result.events.append(
            self._build_loop_started(
                run=run, node=node,
                invocation_id=invocation_id,
                enclosing_payload=enclosing_payload,
                loop_type=loop_type, mode=mode,
                total=total, parallelism=parallelism,
            )
        )

        # NB: ``not total`` — a lazy file_ref is a non-empty dict, so ``not
        # items`` would be False even for an empty collection. Use the count.
        if not total:
            # Empty-items path: emit LoopCompleted(all_done, 0) too
            # so replay sees a bracketed lifecycle, then dispatch
            # the exit edge task.
            result.state_updates[node.id]["output"] = {"results": []}
            result.events.append(
                self._build_loop_completed(
                    run=run, node=node,
                    invocation_id=invocation_id,
                    enclosing_payload=enclosing_payload,
                    total_iterations=0, reason="all_done",
                )
            )
            return self._dispatch_exit(
                node, run, workflow, topology, task_factory, result,
            )

        # Populate loop_var for backward-compat (downstream resolvers
        # still read run.context.variables["loop"]).
        first_item = await self._item_at(items, 0)
        result.state_updates["loop"] = {
            "index": 0, "item": first_item, "total": total,
            "first": True, "last": total == 1, "depth": 0,
        }

        body_entry = self._get_body_entry(node, topology, workflow)
        if body_entry is not None:
            if mode == "parallel":
                batch_size = min(total, parallelism)
                loop_state["next_dispatch_index"] = batch_size
                for i in range(batch_size):
                    item = first_item if i == 0 else await self._item_at(items, i)
                    self._dispatch_iteration_body(
                        i, item, total, enclosing, enclosing_payload,
                        invocation_id, node, run, body_entry,
                        task_factory, result,
                    )
            else:
                self._dispatch_iteration_body(
                    0, first_item, total, enclosing, enclosing_payload,
                    invocation_id, node, run, body_entry,
                    task_factory, result,
                )
        return result

    async def iteration_complete(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        completed_task: Task,
        task_factory: TaskFactory,
        loop_state: dict,
    ) -> NodeHandlerResult:
        """Advance or terminate the loop after an iteration completed.

        Emits ``LoopIterationCompleted`` with the extended Phase
        4c.4.2 fields, checks the Phase 4c.4.5 break flag, re-evaluates
        the condition (for condition loops), then either dispatches
        the next body iteration or emits ``LoopCompleted`` + exit.

        Signature and semantics match the old
        ``LoopNodeHandler._ed_iteration_complete``.
        """
        result = NodeHandlerResult()
        items = loop_state["items"]
        total = loop_state["total"]
        mode = loop_state.get("mode", "sequential")
        completed = loop_state["completed_iterations"]
        _scope = completed_task.get_innermost_scope()
        iteration_idx = (
            int(_scope.iter) if _scope is not None
            else loop_state.get("current_index", 0)
        )
        completed.append(iteration_idx)

        # Accumulate body output for this iteration. The body node's
        # output is already in run.context.variables (written by
        # complete_task_use_case or apply_state_updates before
        # orchestrate_loop_iteration enqueued the LoopIterationAction).
        body_entry = self._get_body_entry(node, topology, workflow)
        if body_entry is not None:
            body_output = self._variables_view(run).get(body_entry.id, {})
            await self._accumulate_result(loop_state, {
                "index": iteration_idx,
                "item": (
                    await self._item_at(items, iteration_idx)
                    if iteration_idx < total else None
                ),
                "output": body_output,
            })

        invocation_id = loop_state.get("invocation_id", "")
        enclosing_payload: list[dict[str, Any]] = list(
            loop_state.get("enclosing_scope_stack", [])
        )
        just_done_scope = LoopScope(
            loop_node_id=node.id, invocation_id=invocation_id,
            iter=iteration_idx, total=total,
        )

        # Determine iteration outcome. If any body node in this
        # iteration was recorded as FAILED by the engine's
        # maybe_enqueue_loop_iteration hook, the outcome is "failed".
        iter_failed = loop_state.get("iter_failed_nodes", {})
        failed_in_iter = iter_failed.get(iteration_idx, [])
        if failed_in_iter:
            iteration_outcome = "failed"
            failed_iterations = loop_state.setdefault("failed_iterations", [])
            if iteration_idx not in failed_iterations:
                failed_iterations.append(iteration_idx)
        else:
            iteration_outcome = "ok"

        result.events.append(
            LoopIterationCompleted(
                event_id=EventId.derive(
                    causation_id=run.id,
                    correlation_key=(
                        f"loop.iteration.completed:{node.id}:"
                        f"{invocation_id}:{iteration_idx}"
                    ),
                ),
                run_id=run.id,
                workflow_id=run.workflow_id,
                node_id=node.id,
                iteration=iteration_idx,
                total=total,
                invocation_id=invocation_id,
                scope_stack=enclosing_payload + [just_done_scope.to_dict()],
                iter=iteration_idx,
                outcome=iteration_outcome,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
            )
        )

        # Phase 4c.4.5 — break observation. A LOOP_EXIT node inside
        # the body sets this flag on the loop_state during the
        # iteration. Break takes priority over advancement or
        # condition re-evaluation. Forcing ``total = len(completed)``
        # makes the walker's ``completed_count >= total`` check
        # fire on the next re-read so the exit task is dispatched
        # via the completion branch.
        if loop_state.get("break_requested", False):
            loop_state["total"] = len(completed)
            return await self._finalize_and_emit(
                node, run, workflow, topology, task_factory,
                loop_state, invocation_id, enclosing_payload,
                len(completed), "break", result,
            )

        # Continue observation. A LOOP_CONTINUE node inside the body
        # sets this flag during the iteration. Unlike break, continue
        # does NOT terminate the loop — it resets the flag, marks the
        # iteration outcome as "skipped", and falls through to the
        # sequential/parallel advancement logic below (skipping
        # condition re-evaluation so the loop always advances).
        skip_condition_reeval = False
        if loop_state.get("continue_requested", False):
            loop_state["continue_requested"] = False
            skip_condition_reeval = True
            # Update the LoopIterationCompleted event we just appended
            for evt in reversed(result.events):
                if isinstance(evt, LoopIterationCompleted):
                    evt.outcome = "skipped"
                    break

        # Condition-based loop: re-evaluate condition after each
        # iteration (unless continue was requested — continue always
        # advances regardless of condition).
        if not skip_condition_reeval:
            loop_type = loop_state.get("loop_type", "collection")
            if loop_type == "condition":
                condition_expr = loop_state.get("condition", "")
                if condition_expr:
                    try:
                        cond_result = await self._resolver.resolve(
                            condition_expr, self._variables_view(run),
                        )
                        if not cond_result:
                            # Condition-false fix: force total so the
                            # legacy walker's completion check fires.
                            loop_state["total"] = len(completed)
                            return await self._finalize_and_emit(
                                node, run, workflow, topology, task_factory,
                                loop_state, invocation_id, enclosing_payload,
                                len(completed), "condition_false", result,
                            )
                    except VariableResolutionError:
                        loop_state["total"] = len(completed)
                        return await self._finalize_and_emit(
                            node, run, workflow, topology, task_factory,
                            loop_state, invocation_id, enclosing_payload,
                            len(completed), "early_exit_error", result,
                        )

        if mode == "sequential":
            next_index = iteration_idx + 1
            if next_index < total:
                next_item = await self._item_at(items, next_index)
                result.state_updates["loop"] = {
                    "index": next_index, "item": next_item,
                    "total": total, "first": False,
                    "last": next_index == total - 1, "depth": 0,
                }
                loop_state["current_index"] = next_index
                result.loop_state_updates = {node.id: loop_state}
                body_entry = self._get_body_entry(node, topology, workflow)
                if body_entry is not None:
                    enclosing = [
                        LoopScope.from_dict(d) for d in enclosing_payload
                    ]
                    self._dispatch_iteration_body(
                        next_index, next_item, total, enclosing, enclosing_payload,
                        invocation_id, node, run, body_entry,
                        task_factory, result,
                    )
                return result
        else:
            next_index = loop_state.get("next_dispatch_index", len(completed))
            if next_index < total:
                body_entry = self._get_body_entry(node, topology, workflow)
                if body_entry is not None:
                    enclosing = [
                        LoopScope.from_dict(d) for d in enclosing_payload
                    ]
                    next_item = await self._item_at(items, next_index)
                    self._dispatch_iteration_body(
                        next_index, next_item, total, enclosing, enclosing_payload,
                        invocation_id, node, run, body_entry,
                        task_factory, result,
                    )
                    loop_state["next_dispatch_index"] = next_index + 1
                    result.loop_state_updates = {node.id: loop_state}
                    return result

        if len(completed) >= total:
            return await self._finalize_and_emit(
                node, run, workflow, topology, task_factory,
                loop_state, invocation_id, enclosing_payload,
                len(completed), "all_done", result,
            )
        result.loop_state_updates = {node.id: loop_state}
        return result

    # ──────────────── Phase 5.2.2 queued-mode entry ──────────────────

    async def initialize_loop_queued(
        self,
        loop_node: WorkflowNode,
        loop_task: Task,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        *,
        task_factory: TaskFactory,
        enqueue_or_dispatch: EnqueueOrDispatch,
        publish_events: PublishEvents,
    ) -> None:
        """Queued-mode initialisation: emit ``LoopStarted`` + first
        ``LoopIterationStarted``, enqueue the first body task(s) via
        the orchestration queue, initialise ``iter_progress`` on
        loop_state, and return.

        Unlike :py:meth:`run_loop` (which drives the full iteration
        sequence inline via a while-loop), this method does the
        **init phase only**. Iteration advancement happens externally
        via the ``LoopIterationAction`` mechanism: when a body task
        finishes, the engine's ``maybe_enqueue_loop_iteration`` hook
        detects the completion (using ``scope_stack`` + ``iter_progress``
        + the runtime BFS) and enqueues a ``LoopIterationAction``, which
        the worker dispatches to ``OrchestrationEngine.orchestrate_loop_iteration``,
        which re-invokes ``LoopNodeHandler.handle`` (delegating to
        :py:meth:`iteration_complete`) to advance the loop.

        Called by :py:meth:`OrchestrationEngine._exec_uniform`'s
        LOOP + driver branch when ``engine._queue is not None``.
        """
        # Reuse the handler-interface initializer — it produces the
        # same init events and first-body tasks as the inline path.
        init_result = await self.initialize_loop(
            node=loop_node,
            run=run,
            workflow=workflow,
            topology=topology,
            completed_task=loop_task,
            task_factory=task_factory,
        )
        # Apply loop_state updates so ``iter_progress`` can be
        # attached to the entry we just created.
        if init_result.loop_state_updates:
            run.context.loop_state.update(init_result.loop_state_updates)
        # Initialise iter_progress on the new loop_state entry.
        # This is the ONLY new field; no precomputed body_subgraph.
        loop_state = run.context.loop_state.get(loop_node.id)
        if loop_state is not None:
            loop_state["iter_progress"] = {}  # iter_idx -> list[node_id]
            # Seed iter 0's progress bucket for sequential mode or
            # all currently-dispatched iter buckets for parallel mode.
            total = loop_state.get("total", 0)
            if total > 0:
                mode = loop_state.get("mode", "sequential")
                if mode == "parallel":
                    parallelism = loop_state.get("parallelism", 1)
                    batch_size = min(total, parallelism)
                    for i in range(batch_size):
                        loop_state["iter_progress"][i] = []
                else:
                    loop_state["iter_progress"][0] = []
        # Apply variable updates.
        for key, value in init_result.state_updates.items():
            run.context.variables[key] = value
        # Publish init events IMMEDIATELY (the Phase 5.2.1 ordering
        # fix — LoopStarted lands in the event log before any body
        # events the worker produces asynchronously).
        publish_events(list(init_result.events))
        # Enqueue pending tasks via the injected callable. In queued
        # mode ``enqueue_or_dispatch`` publishes an ``ExecuteNodeAction``
        # and returns immediately — worker picks it up asynchronously.
        for t in init_result.tasks_to_dispatch:
            await enqueue_or_dispatch(t, run, workflow, topology)
        # Empty-collection short-circuit: the loop has no iterations,
        # so it's already done. Mark the loop node completed so
        # downstream predecessor-readiness checks pass.
        if loop_state is None or loop_state.get("total", 0) == 0:
            run.context.completed_nodes.add(loop_node.id)

    # ──────────────────────── helpers ────────────────────────────────

    async def _resolve_loop_config(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
    ) -> tuple[Any, str, str, int, str, str]:
        """Resolve the loop's items and extract config. Returns
        ``(items, total, loop_type, mode, parallelism, condition_expr,
        counter_variable)``.

        ``items`` is one of THREE shapes, and every reader goes through
        ``_items_total`` / ``item_at`` rather than handling them itself: a
        concrete ``list``, a ``file_ref`` pointer (a lazy or claim-checked
        collection), or an ``index_range`` marker (``count`` / ``condition``,
        where the index IS the value)."""
        # Canonical keys only (NodeSpec Phase 3): the items expression is
        # `collection` — the legacy `items_expression` alias was
        # normalized away by migration.
        cfg = LoopConfig.from_node(node)
        loop_type = cfg.loop_type
        max_iterations = cfg.max_iterations
        if not isinstance(max_iterations, (int, float)) or isinstance(
            max_iterations, bool
        ):
            max_iterations = 10000
        else:
            max_iterations = int(max_iterations)
        items: Any = []

        if loop_type == "count":
            items = index_range(max(0, min(max_iterations, 100000)))
        elif loop_type == "condition":
            if cfg.condition:
                items = index_range(max_iterations)
        else:
            items_expr = cfg.collection
            if items_expr:
                try:
                    resolved = await self._resolver.resolve(
                        items_expr, self._variables_view(run),
                    )
                    items = await self._coerce_collection(resolved, node)
                except VariableResolutionError as exc:
                    logging.getLogger(__name__).warning(
                        "VariableResolutionError resolving items for loop "
                        "node %s: %s — defaulting to empty list",
                        node.id, exc,
                    )
                    items = []
            # Cap iterations at max_iterations. A concrete list is sliced; a
            # lazy file_ref can't be sliced, so ``total`` carries the cap (rows
            # past it are simply never fetched).
            if isinstance(items, list) and len(items) > max_iterations:
                items = items[:max_iterations]

        # ``total`` is the authoritative iteration count (capped) — for a lazy
        # file_ref it comes from ``row_count`` metadata (no download).
        total = min(self._items_total(items), max_iterations)

        mode = cfg.mode
        counter_variable = cfg.counter_variable
        parallelism = cfg.parallelism
        if (
            not isinstance(parallelism, (int, float))
            or isinstance(parallelism, bool)
            or parallelism < 1
        ):
            # 0 = unbounded → cap at the item count (legacy default).
            parallelism = total if total else 1
        else:
            parallelism = int(parallelism)
        condition_expr = cfg.condition
        return items, total, loop_type, mode, parallelism, condition_expr, counter_variable

    async def _coerce_collection(self, resolved: Any, node: WorkflowNode) -> Any:
        """Coerce a resolved ``collection`` value into an iterable the loop can
        walk — either a concrete ``list`` OR a file_ref pointer (lazy).

        A loop is a *whole-value consumer*. The cases:

        * **inline list** → passes straight through (the store is never
          consulted — no needless download for the common case).
        * **LAZY file-backed reference** (SA-1905 D2.4): a flat file-backed
          list (``is_lazy_range_eligible`` — a streamed N-row collection) is
          returned AS THE POINTER. The loop reads one row at a time by index
          via ``materialize_range`` (``_item_at``) and never holds the whole
          array in RAM (nor in the persisted ``loop_state``). This removes the
          CP OOM a large loop over a streamed collection used to hit.
        * **non-flat file-backed reference** (recursive nested arrays, object
          children) → full ``materialize`` fallback — per-row reconstruction
          isn't supported for those, and they're bounded by ``max_iterations``.
        * **a declared non-array** → a typed error, NOT an empty list.
        * **anything else inline** (scalar/dict/None) → empty, same as before.

        SA-1977 — collection-ness is now decided from the DECLARED kind, before
        any I/O. This method used to answer *"is this a collection?"* by calling
        ``materialize()`` and running ``isinstance`` on the result: it paid the
        exact whole-value download the lazy path exists to avoid, and it answered
        differently depending on whether the value happened to have artifacts —
        so ONE collection of ONE item gave two different wrong answers (a nested
        array → 0 iterations and a green run that did nothing; no nested array →
        the pointer reached the engine and raised ``KeyError: 0``).

        A **declared object** now fails fast and loudly. Silently iterating zero
        times is the worst possible outcome: the run goes green having done
        nothing, and nobody looks at it again.

        A **legacy ref** (written before the contract, so no ``value_kind``) is
        read as an ARRAY OF ITS ROWS — which is what a CSV table pointed at by a
        LOOP means. This is the one place the legacy fallback is *not* today's
        behaviour, deliberately: today a 1-row legacy collection either crashes
        (lazy) or silently runs zero iterations (eager). One iteration is the
        only defensible reading of both.

        Returning the file_ref keeps ``loop_state`` serializable (a file_ref is
        a plain dict) — the pointer, not a live view, crosses the event-sourcing
        boundary; each pass rebuilds the lazy read from it.
        """
        if isinstance(resolved, list):
            return resolved
        if is_file_ref(resolved):
            if self._store is None:
                logging.getLogger(__name__).warning(
                    "Loop node %s: collection resolved to a file-backed "
                    "reference (file_id=%s) but no DataStoreService is wired — "
                    "cannot materialize; defaulting to empty. Wire "
                    "data_store_service to iterate file-backed collections.",
                    node.id, resolved.get("file_id", ""),
                )
                return []
            kind = kind_of(resolved)
            if kind not in (ARRAY, UNKNOWN, NULL):
                return self._reject_non_collection(node, kind)
            if kind == NULL:
                return []
            if self._store.is_lazy_range_eligible(resolved):
                return resolved  # LAZY — rows fetched on demand by index.
            materialized = await self._store.materialize(resolved)
            if isinstance(materialized, list):
                return materialized
            if kind == UNKNOWN and isinstance(materialized, dict):
                # Legacy ref, one row. The bytes cannot say whether that row was
                # a 1-element list or an object, so read it as the table it is.
                return [materialized]
            logging.getLogger(__name__).warning(
                "Loop node %s: file-backed collection materialized to %s, not "
                "a list; defaulting to empty.",
                node.id, type(materialized).__name__,
            )
            return []

        # Inline (not file-backed). The SAME authoring mistake must give the SAME
        # answer here, or the fail-fast is decided by whether the upstream node
        # happened to be file-backed — which is the bug family, not the fix.
        #
        # A ``code`` node keeps its output INLINE (``should_keep_inline``), so
        # ``LOOP over {{$codeNode.out}}`` where ``out`` is an object took this path
        # and ran zero iterations, green, while the identical mistake against an
        # http-worker's (file-backed) object now fails loudly.
        #
        # ``None`` is the deliberate exception: an absent/unresolved value means
        # "nothing to iterate", which is a legitimate empty run and is what a
        # file-backed ``null`` (value_kind=null) does too. Everything else — a
        # dict, a string, a number — is an authoring error.
        if resolved is None:
            return []
        return self._reject_non_collection(node, kind_of_inline(resolved))

    @staticmethod
    def _reject_non_collection(node: WorkflowNode, kind: str) -> list:
        """A LOOP's collection is not a collection. Fail loudly — or, if the
        operator has switched strictness off, do the old thing but SAY SO."""
        if _strict_collection():
            raise LoopCollectionTypeError(node.id, kind)
        logging.getLogger(__name__).warning(
            "Loop node %s: 'collection' resolved to a %s, not an array — running "
            "ZERO iterations (the loop body will not execute and the run will "
            "report COMPLETED). This is almost certainly an authoring error; it "
            "raises a typed error when LOOP_STRICT_COLLECTION is on (the default).",
            node.id, kind,
        )
        return []

    def _items_total(self, items: Any) -> int:
        """Length of a resolved collection — ``row_count`` metadata for a lazy
        file_ref (no download), ``count`` for a synthetic range, ``len`` for a
        concrete list.

        ``len`` on a file_ref would return its KEY COUNT — which is why the
        engine's bounds guard (``iter_i < len(loop_items)``) passed on a pointer
        and let ``loop_items[0]`` through to raise ``KeyError: 0``."""
        if is_file_ref(items):
            return int(items.get("row_count", 0) or 0)
        if is_index_range(items):
            return int(items.get(RANGE_COUNT_KEY, 0) or 0)
        if isinstance(items, (list, tuple)):
            return len(items)
        return 0

    async def item_at(self, items: Any, index: int) -> Any:
        """The item at ``index`` — a single-row OData fetch for a lazy file_ref
        (never downloads the whole collection), plain indexing for a list.
        Returns ``None`` for an out-of-range / missing lazy row.

        Public because the ENGINE needs it too. ``loop_state["items"]`` is either
        a list or a file_ref pointer, and the engine used to index it directly
        (``items[i]``) — which is fine for a list and raises ``KeyError: 0`` on a
        pointer. There is exactly one way to read an item out of a collection,
        and this is it; a second one is how the bug got in.
        """
        if is_file_ref(items):
            if self._store is None:
                return None
            rows = await self._store.materialize_range(items, index, 1)
            return rows[0] if rows else None
        if is_index_range(items):
            total = int(items.get(RANGE_COUNT_KEY, 0) or 0)
            return index if 0 <= index < total else None
        if isinstance(items, (list, tuple)):
            return items[index] if 0 <= index < len(items) else None
        return None

    # Internal alias — the driver's own call sites predate the public name.
    _item_at = item_at

    async def _accumulate_result(self, loop_state: dict, entry: dict[str, Any]) -> None:
        """Append one iteration result, spilling a full window to a durable
        chunk file_ref when spilling is enabled (F2). Bounds in-RAM growth to
        ``self._results_window`` entries. Items/outputs that are already
        file_refs are kept as pointers (F1 preserves them on store)."""
        window = loop_state.setdefault("iteration_results", [])
        window.append(entry)
        if self._spill_enabled() and self._window_is_full(window):
            await self._spill_window(loop_state)

    def _window_is_full(self, window: list) -> bool:
        """Either axis trips it — entry count OR accumulated bytes.

        Count alone never fired in production: the largest loop that has ever
        run on tenant-1 has 100 iterations against a 1000-entry gate, while its
        results were 65% of all loop_state bytes. See
        ``_ITERATION_RESULTS_MAX_BYTES``.

        Measured with ``default=str`` for the same reason
        ``inline_output_size_bytes`` does: a value that will not strictly
        serialise still has to be MEASURED, not crash the check. Unmeasurable
        counts as full — failing closed keeps an exotic value out of
        ``loop_state`` rather than gambling that it fits.
        """
        if len(window) >= self._results_window:
            return True
        try:
            return len(json.dumps(window, default=str).encode("utf-8")) >= \
                self._results_max_bytes
        except Exception:
            # Deliberately broad, and narrower would be wrong. ``default=str``
            # calls ``repr`` on anything unserialisable, so a value whose own
            # ``__repr__`` raises escapes a (TypeError, ValueError) clause —
            # and this runs on EVERY iteration of EVERY loop, so an escape here
            # takes the run down. Measuring is best-effort; the decision is
            # not. Unmeasurable counts as full: spilling something exotic is
            # cheap, keeping it in ``loop_state`` is what this exists to stop.
            return True

    async def _claim_check_items(self, items: Any, invocation_id: str) -> Any:
        """Turn an oversized inline collection into a ``file_ref`` pointer.

        ``items`` is 35% of every byte of ``loop_state`` ever stored here, and
        3 of the 4 measured loops hold it as a concrete list. Once ``loop_state``
        lives in ``AIW_RUN_LOOP_STATE`` those lists have to fit
        ``STATE_JSON NVARCHAR(5000)``, so anything past ~4 KB becomes a pointer
        and the row carries a ``file_id`` instead of the collection.

        Deliberately NOT unconditional. ``item_at`` reads a pointer one row at a
        time through OData, so claim-checking a 4-item loop would trade zero HTTP
        calls for four on the loop's hot path — and ``total`` is p50 **4** across
        all 23 loops ever run on this tenant. The threshold IS the design.

        Nothing downstream needs to know which shape came back: every read of
        ``items`` already goes through ``item_at`` / ``_items_total``, which
        handle both. That single funnel is what makes this change two lines at
        the call site instead of an audit.

        On a store failure the collection stays inline — exactly what
        ``_spill_window`` does. A loop that runs with a fat ``loop_state`` is a
        cost; a loop that fails to start is an outage.
        """
        if not isinstance(items, list) or not items or self._store is None:
            return items
        try:
            size = len(json.dumps(items, default=str).encode("utf-8"))
        except Exception:
            # Unmeasurable counts as oversized, the same way ``_window_is_full``
            # fails closed: storing it is cheap, keeping it is what this exists
            # to stop.
            size = self._items_max_inline_bytes + 1
        if size <= self._items_max_inline_bytes:
            return items
        try:
            ref = await self._store.store_data(f"loop-items-{invocation_id}", items)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "loop items claim-check failed for %s (%d bytes stay inline): %s",
                invocation_id, size, exc,
            )
            return items
        return ref if is_file_ref(ref) else items

    async def _spill_window(self, loop_state: dict) -> None:
        """Flush the current iteration-results window to a durable chunk CSV and
        clear it. Records lightweight chunk metadata on loop_state (serializable
        → replay-safe) instead of the rows themselves. On a store failure the
        window is kept inline (no data loss)."""
        window = loop_state.get("iteration_results", [])
        if not window:
            return
        chunks = loop_state.setdefault("iteration_result_chunks", [])
        invocation_id = loop_state.get("invocation_id", "loop")
        key = f"loop-results-{invocation_id}-{len(chunks)}"
        try:
            ref = await self._store.store_data(key, list(window))
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Failed to spill loop iteration_results chunk (%s); keeping "
                "this window inline", exc,
            )
            return
        chunks.append({
            "file_id": ref.get("file_id", ""),
            "row_count": ref.get("row_count", len(window)),
            "columns": ref.get("columns", []),
            "column_types": ref.get("column_types", {}),
        })
        loop_state["iteration_results"] = []

    async def _finalize_and_emit(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        task_factory: TaskFactory,
        loop_state: dict,
        invocation_id: str,
        enclosing_payload: list[dict[str, Any]],
        total_iterations: int,
        reason: str,
        result: NodeHandlerResult,
    ) -> NodeHandlerResult:
        """F2 — flush any residual window + assemble the chunk manifest before
        the (sync) final emit. A no-op when nothing was spilled, so a small loop
        keeps its inline ``results`` list byte-for-byte."""
        chunks = loop_state.get("iteration_result_chunks", [])
        window = loop_state.get("iteration_results", [])
        if self._spill_enabled() and (chunks or len(window) >= self._results_window):
            if window:
                await self._spill_window(loop_state)
            chunks = loop_state.get("iteration_result_chunks", [])
            if chunks:
                loop_state["iteration_results_final"] = self._build_chunk_manifest(chunks)
        return self._emit_loop_completed_and_exit(
            node, run, workflow, topology, task_factory,
            loop_state, invocation_id, enclosing_payload,
            total_iterations, reason, result,
        )

    @staticmethod
    def _build_chunk_manifest(chunks: list[dict[str, Any]]) -> dict[str, Any]:
        """Build a file_ref referencing the spilled result chunks. It has no
        main CSV (``file_id=""``) — its ``loop_chunk`` artifacts ARE the data;
        ``DataStoreService.materialize`` concatenates them back into the full
        results list."""
        total = sum(int(c.get("row_count") or 0) for c in chunks)
        merged_cols: list[str] = []
        merged_types: dict[str, str] = {}
        for c in chunks:
            for col in c.get("columns", []) or []:
                if col not in merged_cols:
                    merged_cols.append(col)
            for k, v in (c.get("column_types") or {}).items():
                merged_types.setdefault(k, v)
        ref = build_file_ref(
            file_id="", row_count=total,
            columns=merged_cols, column_types=merged_types,
            # SA-1977 — a loop-results manifest IS a list of iteration-result
            # objects. Leaving it undeclared worked only because ``UNKNOWN`` is
            # permissive in ``_coerce_collection``: a downstream LOOP over
            # ``{{$loopNode.output.results}}`` was surviving on the legacy escape
            # hatch, and would break the day anyone tightened it.
            value_kind=ARRAY, item_kind=OBJECT,
        )
        for i, c in enumerate(chunks):
            art = add_artifact(
                ref, file_id=c.get("file_id", ""), name=f"chunk_{i}",
                artifact_type=LOOP_CHUNK_ARTIFACT,
                column_types=c.get("column_types") or {},
            )
            art["row_count"] = int(c.get("row_count") or 0)
        return ref

    def _dispatch_iteration_body(
        self,
        i: int,
        item: Any,
        total: int,
        enclosing: list[LoopScope],
        enclosing_payload: list[dict[str, Any]],
        invocation_id: str,
        node: WorkflowNode,
        run: WorkflowRun,
        body_entry: WorkflowNode,
        task_factory: TaskFactory,
        result: NodeHandlerResult,
    ) -> None:
        """Build one body task for iteration ``i`` and emit the paired
        ``LoopIterationStarted`` event.

        Takes the ALREADY-FETCHED ``item`` (+ the loop ``total``) so this stays
        synchronous — the async caller reads the row (lazily for a file-backed
        collection) before dispatching.

        Dual-writes the legacy ``loop_context`` + ``iteration_index``
        fields alongside the new ``scope_stack`` so downstream handlers
        that have not yet migrated keep working.
        """
        iter_scope = LoopScope(
            loop_node_id=node.id, invocation_id=invocation_id,
            iter=i, total=total,
        )
        task = task_factory.create_task(body_entry, run)
        # Phase 5.2.2 — ``task_factory.create_task`` may have
        # already inherited a scope from the ambient contextvar
        # (set by the engine around ``invoke_handler``). We want
        # the authoritative stack here to be exactly
        # ``enclosing + [iter_scope]``, so override any inherited
        # value with an explicit assignment rather than the push-
        # based accumulation which would produce duplicates.
        task.scope_stack = list(enclosing) + [iter_scope]
        # …and the id, for the same reason and by the same means.
        #
        # ``task_id_for`` is ``{run}-{node}`` — no iteration term. In
        # ``sequential`` mode the iterations are separated in time, so reusing
        # the id merely overwrites the previous attempt's row. In ``parallel``
        # mode ``initialize_loop`` builds ``min(total, parallelism)`` of these
        # in ONE pass and they all collide: ``_run_loop_body`` upserts them onto
        # a single row (last write wins, so the row carries the LAST
        # iteration's scope), enqueues N ``ExecuteNodeAction`` naming that one
        # id, and ``_execute_node_inner`` — which guards on ``task.status``, per
        # TASK — runs the first delivery and drops the rest as duplicates. One
        # iteration executes, ``completed_iterations`` never reaches ``total``,
        # and the run hangs RUNNING with the LOOP node stuck READY. Measured on
        # tenant-1 run a522ee2c: five iterations dispatched, one body execution,
        # reporting iter=4 — the one that won the upsert.
        #
        # ``_execute_node_inner``'s own comment already states the invariant
        # this restores: "Legitimate re-runs (loop bodies, cycle revisits)
        # always get a fresh task id".
        #
        # ``invocation_id`` is in the id, not only ``i``: a nested loop's body
        # runs iterations 0..n again on every outer iteration, so ``iter`` alone
        # repeats. Both terms are deterministic, which keeps a redelivered
        # action resolving to the same row.
        task.id = f"{task.id}-{invocation_id}-i{i}"
        task.idempotency_key = f"{task.id}-attempt-0"
        result.tasks_to_dispatch.append(task)

        result.events.append(
            LoopIterationStarted(
                event_id=EventId.derive(
                    causation_id=run.id,
                    correlation_key=(
                        f"loop.iteration.started:{node.id}:"
                        f"{invocation_id}:{i}"
                    ),
                ),
                run_id=run.id, workflow_id=run.workflow_id,
                node_id=node.id, invocation_id=invocation_id,
                scope_stack=enclosing_payload + [iter_scope.to_dict()],
                iter=i, item=item,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
            )
        )

    def _emit_loop_completed_and_exit(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        task_factory: TaskFactory,
        loop_state: dict,
        invocation_id: str,
        enclosing_payload: list[dict[str, Any]],
        total_iterations: int,
        reason: str,
        result: NodeHandlerResult,
    ) -> NodeHandlerResult:
        """Emit ``LoopCompleted`` with the given reason and dispatch
        the loop's exit edge task."""
        result.events.append(
            self._build_loop_completed(
                run=run, node=node,
                invocation_id=invocation_id,
                enclosing_payload=enclosing_payload,
                total_iterations=total_iterations, reason=reason,
            )
        )
        # Build the final loop output with accumulated iteration results.
        # When the results were spilled to durable chunks (F2), the final
        # output is a file_ref manifest instead of the in-RAM list; downstream
        # consumers resolve it via the file-backed resolver / materialize.
        result.state_updates[node.id] = {
            "output": {
                "results": (
                    loop_state.get("iteration_results_final")
                    or loop_state.get("iteration_results", [])
                ),
            },
            "metadata": {
                "type": "loop",
                "total_iterations": total_iterations,
                "reason": reason,
                "mode": loop_state.get("mode", "sequential"),
                "loop_type": loop_state.get("loop_type", "collection"),
            },
        }
        # Prune merge quorum buffers for this loop invocation to avoid
        # unbounded memory in million-iteration workflows. Buffers are
        # keyed on (node_id, invocation_id, iter) — remove all entries
        # matching this loop's invocation_id.
        keys_to_remove = [
            k for k in run.context.merge_quorum_buffers
            if isinstance(k, tuple) and len(k) >= 2
            and k[1] == invocation_id
        ]
        for k in keys_to_remove:
            del run.context.merge_quorum_buffers[k]

        result.loop_state_updates = {node.id: loop_state}
        return self._dispatch_exit(
            node, run, workflow, topology, task_factory, result,
        )

    @staticmethod
    def _build_loop_started(
        *,
        run: WorkflowRun,
        node: WorkflowNode,
        invocation_id: str,
        enclosing_payload: list[dict[str, Any]],
        loop_type: str,
        mode: str,
        total: int,
        parallelism: int,
    ) -> LoopStarted:
        return LoopStarted(
            event_id=EventId.derive(
                causation_id=run.id,
                correlation_key=f"loop.started:{node.id}:{invocation_id}",
            ),
            run_id=run.id,
            workflow_id=run.workflow_id,
            node_id=node.id,
            invocation_id=invocation_id,
            scope_stack=list(enclosing_payload),
            loop_type=loop_type,
            mode=mode,
            total=total,
            parallelism=parallelism,
            root_run_id=run.root_run_id or run.id,
            depth=run.depth_level,
            source="workflow-control-plane",
        )

    @staticmethod
    def _build_loop_completed(
        *,
        run: WorkflowRun,
        node: WorkflowNode,
        invocation_id: str,
        enclosing_payload: list[dict[str, Any]],
        total_iterations: int,
        reason: str,
    ) -> LoopCompleted:
        return LoopCompleted(
            event_id=EventId.derive(
                causation_id=run.id,
                correlation_key=f"loop.completed:{node.id}:{invocation_id}:{reason}",
            ),
            run_id=run.id,
            workflow_id=run.workflow_id,
            node_id=node.id,
            invocation_id=invocation_id,
            scope_stack=list(enclosing_payload),
            total_iterations=total_iterations,
            reason=reason,
            root_run_id=run.root_run_id or run.id,
            depth=run.depth_level,
            source="workflow-control-plane",
        )

    @staticmethod
    def _dispatch_exit(
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        task_factory: TaskFactory,
        result: NodeHandlerResult,
    ) -> NodeHandlerResult:
        """Dispatch the task for the loop's exit edge. Matches the
        logic in ``LoopNodeHandler._dispatch_exit`` exactly."""
        outgoing_edges = topology.edge_map.get(node.id, [])
        for edge in outgoing_edges:
            edge_cond = (edge.condition or "").lower()
            port_id = (edge.source_port_id or "").lower()
            is_exit = (
                edge_cond in ("exit", "complete", "")
                or port_id in ("loop_complete", "exit", "complete")
            )
            is_body = (
                edge_cond == "body"
                or port_id in ("loop_body", "body")
            )
            if is_exit and not is_body:
                target_node = workflow.get_node(edge.target_node_id)
                if target_node is not None:
                    task = task_factory.create_task(target_node, run)
                    result.tasks_to_dispatch.append(task)
                    break
        return result

    @staticmethod
    def _get_body_entry(
        node: WorkflowNode,
        topology: GraphTopology,
        workflow: Workflow,
    ) -> WorkflowNode | None:
        outgoing_edges = topology.edge_map.get(node.id, [])
        for edge in outgoing_edges:
            edge_cond = (edge.condition or "").lower()
            port_id = (edge.source_port_id or "").lower()
            if edge_cond == "body" or port_id in ("loop_body", "body"):
                return workflow.get_node(edge.target_node_id)
        for edge in outgoing_edges:
            edge_cond = (edge.condition or "").lower()
            port_id = (edge.source_port_id or "").lower()
            if edge_cond not in ("exit", "complete") and port_id not in (
                "loop_complete", "exit", "complete",
            ):
                return workflow.get_node(edge.target_node_id)
        return None

