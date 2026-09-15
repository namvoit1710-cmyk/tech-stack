"""LOOP_EXIT node handler — cooperative break signal for event-driven loops.

Phase 4c.4.5 — the runtime emitter for the ``LoopExitRequested`` event
type defined in Phase 4c.4.2. Placed inside a LOOP body (typically
downstream of a CONDITION), firing this node causes the enclosing
event-driven loop to terminate early at the next iteration boundary
with ``reason="break"``.

Design decisions (from the Phase 4c.4.5 clarification Q&A):

- **Targets the innermost enclosing event-driven loop.** No config
  is required on the node — the handler finds the innermost active
  loop by iterating ``run.context.loop_state`` in reverse insertion
  order. Dicts preserve insertion order in Python 3.7+, and
  ``_ed_initialize_loop`` is the only writer, so the last-added entry
  is always the innermost.
- **Observed only when `EVENT_DRIVEN_LOOP=true`.** The filter below
  excludes legacy (non-event-driven) loops by requiring a non-empty
  ``invocation_id``. Legacy loops always have ``invocation_id=""``
  on their loop_state, so a LOOP_EXIT placed inside a legacy-mode
  loop becomes a no-op (logged warning) instead of breaking the run.
- **Cooperative, not pre-emptive.** This handler only sets a flag
  on the loop_state. The loop handler's ``_ed_iteration_complete``
  branch observes the flag at the next iteration boundary and emits
  ``LoopCompleted(reason="break")``. In-flight iterations (e.g.
  other parallel body tasks) finish naturally before the break takes
  effect.
- **Dead-end for this branch.** The handler returns no tasks_to_dispatch
  — LOOP_EXIT is a terminal node for its body branch in the DAG.
  Any outgoing edges on a LOOP_EXIT node are a workflow authoring
  error but do not fail the handler (the successor processor will
  still walk them via compute_fanout, which is acceptable).
"""

from __future__ import annotations

import logging
from typing import Any

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.events import LoopExitRequested
from app.layer1_domain.value_objects.event_id import EventId
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.node_handlers.inline_bases import (
    PureInlineHandlerBase,
)
from app.layer2_application.orchestration.task_factory import TaskFactory


class LoopExitNodeHandler(PureInlineHandlerBase):
    async def handle(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        completed_task: Task | None,
        task_factory: TaskFactory,
    ) -> NodeHandlerResult:
        result = NodeHandlerResult()

        # Phase 6 — uniform execution fields.
        result.output_port = "out_default"
        result.completion_mode = "immediate"

        target = self._find_innermost_active_loop(run)
        if target is None:
            # No active event-driven loop — either the break node was
            # placed outside any loop, or EVENT_DRIVEN_LOOP is off and
            # every active loop is legacy (empty invocation_id). Log
            # and no-op so the run continues.
            logging.getLogger(__name__).warning(
                "LOOP_EXIT node %s fired with no innermost active "
                "event-driven loop in run %s — no break signal emitted. "
                "Check that the node is inside a loop body and that "
                "EVENT_DRIVEN_LOOP is enabled.",
                node.id,
                run.id,
            )
            result.state_updates[node.id] = {
                "output": {"break_requested": False},
                "metadata": {
                    "type": "loop_exit",
                    "status": "noop",
                    "reason": "no_active_event_driven_loop",
                },
            }
            return result

        loop_node_id, invocation_id, loop_state = target

        # Mutate the loop_state in place — the event-driven loop
        # handler's ``_ed_iteration_complete`` will observe this flag
        # at the next iteration boundary and emit LoopCompleted.
        loop_state["break_requested"] = True

        # Reconstruct the scope_stack payload for the event. We carry
        # the enclosing scopes that the loop was initialised with,
        # plus the loop's own current scope at the tail — matching
        # the convention used by other loop events.
        enclosing_payload: list[dict[str, Any]] = list(
            loop_state.get("enclosing_scope_stack", [])
        )
        current_iter = loop_state.get("current_index", 0)
        total = loop_state.get("total", 0)
        self_scope_payload = {
            "loop_node_id": loop_node_id,
            "invocation_id": invocation_id,
            "iter": int(current_iter or 0),
            "total": int(total or 0),
        }

        result.events.append(
            LoopExitRequested(
                event_id=EventId.derive(
                    causation_id=run.id,
                    correlation_key=(
                        f"loop.exit.requested:{loop_node_id}:{invocation_id}"
                    ),
                ),
                run_id=run.id,
                workflow_id=run.workflow_id,
                loop_node_id=loop_node_id,
                invocation_id=invocation_id,
                scope_stack=enclosing_payload + [self_scope_payload],
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
            )
        )

        result.state_updates[node.id] = {
            "output": {
                "break_requested": True,
                "loop_node_id": loop_node_id,
                "invocation_id": invocation_id,
            },
            "metadata": {"type": "loop_exit", "status": "requested"},
        }
        return result

    @staticmethod
    def _find_innermost_active_loop(
        run: WorkflowRun,
    ) -> tuple[str, str, dict[str, Any]] | None:
        """Return ``(loop_node_id, invocation_id, loop_state)`` for the
        innermost active event-driven loop, or ``None`` if no such loop
        exists.

        Dict insertion order gives us the nesting order: outer loops
        are initialised first (``_ed_initialize_loop``), nested inner
        loops are added later. Iterating in reverse finds the innermost
        first.

        Filters applied:
        - ``invocation_id`` must be non-empty. Legacy loops have
          ``invocation_id=""`` on their loop_state (they never set the
          field) — excluding them implements the "Flag ON only"
          semantics for break.
        - ``len(completed_iterations) < total`` — the loop must still
          be running. Already-completed loops stay in ``loop_state``
          forever, so we filter them out.
        - ``break_requested`` must be False. If a break was already
          requested for this loop (e.g. two LOOP_EXIT nodes in the
          same iteration), a second request would target the same
          loop or, without this filter, leak to an outer loop. We
          keep the behavior idempotent: re-breaking the already-broken
          innermost is a no-op (returns None, caller logs warning).

        Note: ``current_index`` is the iteration that just
        DISPATCHED — the one the body is running right now. Before
        the iteration-complete callback is invoked,
        ``len(completed_iterations)`` is strictly less than
        ``current_index + 1 <= total``, so a running loop always
        satisfies the filter.
        """
        for loop_node_id, loop_state in reversed(list(run.context.loop_state.items())):
            if not isinstance(loop_state, dict):
                continue
            if loop_state.get("break_requested", False):
                continue
            invocation_id = str(loop_state.get("invocation_id", "") or "")
            if not invocation_id:
                continue  # legacy / non-event-driven loop
            total = int(loop_state.get("total", 0) or 0)
            completed = loop_state.get("completed_iterations", [])
            if not isinstance(completed, list):
                completed = []
            if len(completed) >= total:
                continue  # loop has already finished naturally
            return loop_node_id, invocation_id, loop_state
        return None
