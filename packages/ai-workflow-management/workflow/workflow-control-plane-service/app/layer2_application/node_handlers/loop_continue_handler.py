"""LOOP_CONTINUE node handler — cooperative continue signal for event-driven loops.

Mirrors the ``LoopExitNodeHandler`` (Phase 4c.4.5 break) but with
the opposite effect: instead of terminating the loop, LOOP_CONTINUE
skips the rest of the current iteration's body and advances directly
to the next iteration. The loop driver observes ``continue_requested``
on ``loop_state`` at the next iteration boundary, resets the flag,
marks the iteration outcome as ``"skipped"``, and dispatches the
next body without re-evaluating the condition.

Design decisions:

- **Targets the innermost enclosing event-driven loop.** Same
  discovery logic as ``LoopExitNodeHandler._find_innermost_active_loop``
  — scans ``run.context.loop_state`` in reverse insertion order,
  filtering for non-empty ``invocation_id``, active (not finished),
  and not already continue-requested.
- **Observed only when `EVENT_DRIVEN_LOOP=true`.** Legacy loops with
  empty ``invocation_id`` are excluded.
- **Cooperative, not pre-emptive.** The handler only sets a flag;
  the driver observes it at the next iteration boundary.
- **Dead-end for this branch.** Returns no ``tasks_to_dispatch``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.events import LoopContinueRequested
from app.layer1_domain.value_objects.event_id import EventId
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.node_handlers.inline_bases import (
    PureInlineHandlerBase,
)
from app.layer2_application.orchestration.task_factory import TaskFactory


class LoopContinueNodeHandler(PureInlineHandlerBase):
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
            logging.getLogger(__name__).warning(
                "LOOP_CONTINUE node %s fired with no innermost active "
                "event-driven loop in run %s — no continue signal emitted. "
                "Check that the node is inside a loop body and that "
                "EVENT_DRIVEN_LOOP is enabled.",
                node.id,
                run.id,
            )
            result.state_updates[node.id] = {
                "output": {"continue_requested": False},
                "metadata": {
                    "type": "loop_continue",
                    "status": "noop",
                    "reason": "no_active_event_driven_loop",
                },
            }
            return result

        loop_node_id, invocation_id, loop_state = target

        loop_state["continue_requested"] = True

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
            LoopContinueRequested(
                event_id=EventId.derive(
                    causation_id=run.id,
                    correlation_key=(
                        f"loop.continue.requested:{loop_node_id}:{invocation_id}"
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
                "continue_requested": True,
                "loop_node_id": loop_node_id,
                "invocation_id": invocation_id,
            },
            "metadata": {"type": "loop_continue", "status": "requested"},
        }
        return result

    @staticmethod
    def _find_innermost_active_loop(
        run: WorkflowRun,
    ) -> tuple[str, str, dict[str, Any]] | None:
        """Return ``(loop_node_id, invocation_id, loop_state)`` for the
        innermost active event-driven loop that hasn't already been
        continue-requested, or ``None``.

        Same discovery logic as ``LoopExitNodeHandler``:
        - ``invocation_id`` must be non-empty (excludes legacy loops).
        - ``len(completed_iterations) < total`` (loop still running).
        - ``continue_requested`` must be False (idempotent).
        - ``break_requested`` must be False (break takes priority).
        """
        for loop_node_id, loop_state in reversed(list(run.context.loop_state.items())):
            if not isinstance(loop_state, dict):
                continue
            if loop_state.get("break_requested", False):
                continue
            if loop_state.get("continue_requested", False):
                continue
            invocation_id = str(loop_state.get("invocation_id", "") or "")
            if not invocation_id:
                continue
            total = int(loop_state.get("total", 0) or 0)
            completed = loop_state.get("completed_iterations", [])
            if not isinstance(completed, list):
                completed = []
            if len(completed) >= total:
                continue
            return loop_node_id, invocation_id, loop_state
        return None
