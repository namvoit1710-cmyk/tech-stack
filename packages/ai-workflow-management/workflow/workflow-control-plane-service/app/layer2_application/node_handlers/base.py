"""Node-handler shape enum, result container, registry, and timing helper.

The ABC tree itself lives in sibling files (``base_node_handler.py``,
``inline_bases.py``, ``routing_base.py``, ``dispatching_base.py``,
``pending_base.py``). This file keeps the *infrastructure* — the
classification enum read by the metrics wrapper, the result
container handlers populate, the registry used at dispatch, and the
``invoke_handler`` timing helper.

The ``NodeHandler`` / ``PendingNodeHandler`` Protocols that used to
live here were retired in the ABC refactor; ``BaseNodeHandler`` is
the authoritative contract. They are re-exported below so import
sites that historically used ``from ...node_handlers.base import
NodeHandler`` keep working.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.events import DomainEvent
from app.layer1_domain.exceptions import UnsupportedNodeTypeError

if TYPE_CHECKING:
    from app.layer1_domain.entities.task import Task
    from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
    from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
    from app.layer2_application.node_handlers.base_node_handler import (
        BaseNodeHandler,
    )
    from app.layer2_application.orchestration.task_factory import TaskFactory

_logger = logging.getLogger(__name__)


class NodeHandlerShape(str, Enum):
    """Classification of how a node handler executes.

    Used as a metric label on ``node_handler_duration_seconds`` so
    operators can distinguish CPU-only handlers from I/O-bound ones
    without inspecting per-node-type timings one by one. The four
    categories match the `Phase 2` taxonomy in the event-driven
    execution plan:

    - ``PURE_INLINE`` — no resolver calls, no I/O. MERGE, TRIGGER.
    - ``ASYNC_INLINE`` — may await the variable resolver which in
      turn may download from the file service. CONDITION, SWITCH,
      LOOP, PARALLEL, WORKFLOW, COMPUTE.
    - ``DISPATCH_EXTERNAL`` — hands work off to a worker via the
      messaging layer. TASK.
    - ``PENDING_USER`` — pauses the run and waits for a user action.
      INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD.
    """

    PURE_INLINE = "pure_inline"
    ASYNC_INLINE = "async_inline"
    DISPATCH_EXTERNAL = "dispatch_external"
    PENDING_USER = "pending_user"
    UNKNOWN = "unknown"


@dataclass
class NodeHandlerResult:
    tasks_to_dispatch: list[Task] = field(default_factory=list)
    skipped_node_ids: list[str] = field(default_factory=list)
    state_updates: dict = field(default_factory=dict)
    loop_state_updates: dict | None = None
    events: list[DomainEvent] = field(default_factory=list)
    run_status_change: RunStatus | None = None
    requires_child_run: bool = False
    # Maps edge_id → source_port_id for edges the handler selected.
    # Used by the unified edge emitter so CONDITION/SWITCH can specify
    # which branch port was taken without emitting EdgeTraversed directly.
    taken_edge_ports: dict[str, str] = field(default_factory=dict)

    # Extra keyword args merged into the ``NodeCompleted`` event by the
    # ``_exec_uniform`` immediate path. CONDITION / SWITCH use this to
    # carry ``branch`` / ``expression`` / ``condition_result`` (folded
    # from the former ConditionEvaluated event). Keys must match
    # ``NodeCompleted`` fields.
    completion_payload: dict[str, Any] = field(default_factory=dict)

    # --- Phase 6: Uniform execution model fields ---
    # These fields are used by the ``_exec_uniform`` engine path when
    # ``UNIFORM_EXECUTION=true``. The legacy engine path ignores them.
    # All have defaults so existing handler code is unaffected.

    # Which output port the handler completed on.
    # Simple nodes: "out_default". Port-routed: "success"/"failure".
    # Branching nodes use ``taken_edge_ports`` instead.
    output_port: str = "out_default"

    # How the engine should react after the handler returns:
    #   "immediate"  — node is done, route to successors now
    #   "dispatched" — node dispatched to external worker, wait for callback
    #   "pending"    — node paused for user input, wait for resume
    #   "child_run"  — node started a child workflow, wait for child
    #   "loop_body"  — node started loop iterations, wait for LoopIterationAction
    #   "deferred"   — no routing (e.g. MERGE quorum not met), save state only
    completion_mode: str = "immediate"

    # For "pending" mode: the PENDING task to save.
    pending_task: Task | None = None
    # For "dispatched" mode: resolved input + worker type for dispatch.
    resolved_input: dict | None = None
    worker_type: str = ""
    # For "child_run" mode: metadata the engine needs to start the child.
    child_workflow_metadata: dict = field(default_factory=dict)
    # For "loop_body" mode: body entry tasks to enqueue.
    loop_body_tasks: list[Task] = field(default_factory=list)

    def merge(self, other: NodeHandlerResult) -> None:
        self.tasks_to_dispatch.extend(other.tasks_to_dispatch)
        self.skipped_node_ids.extend(other.skipped_node_ids)
        self.state_updates.update(other.state_updates)
        if other.loop_state_updates:
            if self.loop_state_updates is None:
                self.loop_state_updates = {}
            self.loop_state_updates.update(other.loop_state_updates)
        self.events.extend(other.events)
        if other.run_status_change is not None:
            self.run_status_change = other.run_status_change
        if other.requires_child_run:
            self.requires_child_run = True
        self.taken_edge_ports.update(other.taken_edge_ports)
        self.completion_payload.update(other.completion_payload)


class NodeHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[NodeType, "BaseNodeHandler"] = {}

    def register(self, node_type: NodeType, handler: "BaseNodeHandler") -> None:
        self._handlers[node_type] = handler

    def get(self, node_type: NodeType) -> "BaseNodeHandler":
        handler = self._handlers.get(node_type)
        if not handler:
            raise UnsupportedNodeTypeError(f"No handler registered for {node_type}")
        return handler


# ---------------------------------------------------------------------------
# Timing helper — wraps ``handler.handle(...)`` with a histogram observation
# ---------------------------------------------------------------------------
#
# Every orchestration call site that invokes a handler uses this helper so
# the ``node_handler_duration_seconds`` histogram stays populated from a
# single code path. The helper is intentionally async (it awaits the real
# ``handle`` coroutine) and takes an optional ``IMonitor`` — when the
# monitor is ``None``, the helper degrades to a straight pass-through with
# zero measurement overhead.
#
# Tags emitted on each observation:
#   handler_shape : NodeHandlerShape value ("pure_inline", "async_inline",
#                   "dispatch_external", "pending_user", "unknown")
#   node_type     : the node's ``NodeType`` value string
#   status        : "ok" if the handler returned normally, "error" if it
#                   raised. Status is set in the finally block so callers
#                   still see exceptions propagate unchanged.
#
# Metric emission is exception-safe — any failure in the monitor call is
# swallowed and logged at debug level. Metrics must never break orchestration.
# ---------------------------------------------------------------------------


async def invoke_handler(
    handler: "BaseNodeHandler",
    *,
    node: "WorkflowNode",
    run: "WorkflowRun",
    workflow: "Workflow",
    topology: "GraphTopology",
    completed_task: "Task | None",
    task_factory: "TaskFactory",
    monitor: Any = None,
    current_scope_stack: Any = None,
) -> "NodeHandlerResult":
    """Invoke a node handler and record the duration as a histogram.

    Phase 5.2.2 — ``current_scope_stack`` is set on the ambient
    task-factory contextvar before invoking the handler so successor
    tasks the handler creates (via ``task_factory.create_task``)
    automatically inherit the enclosing loop scope. Engine call sites
    pass the currently-dispatched task's ``scope_stack``; for
    builtins that don't have a task (e.g. synthetic loop iteration
    feedback), the call site passes ``None``.
    """
    from app.layer2_application.orchestration.task_factory import (
        set_current_scope_stack,
        reset_current_scope_stack,
    )

    start = time.perf_counter()
    status = "ok"
    scope_token = set_current_scope_stack(current_scope_stack)
    try:
        return await handler.handle(
            node=node,
            run=run,
            workflow=workflow,
            topology=topology,
            completed_task=completed_task,
            task_factory=task_factory,
        )
    except BaseException:
        status = "error"
        raise
    finally:
        reset_current_scope_stack(scope_token)
        if monitor is not None and hasattr(monitor, "histogram"):
            try:
                elapsed = time.perf_counter() - start
                shape = getattr(handler, "shape", NodeHandlerShape.UNKNOWN)
                shape_value = getattr(shape, "value", str(shape))
                node_type = getattr(node, "node_type", None)
                node_type_value = (
                    getattr(node_type, "value", str(node_type))
                    if node_type is not None
                    else "unknown"
                )
                monitor.histogram(
                    "node_handler_duration_seconds",
                    elapsed,
                    tags={
                        "handler_shape": shape_value,
                        "node_type": node_type_value,
                        "status": status,
                    },
                )
            except Exception:
                _logger.debug(
                    "monitor.histogram raised; swallowed",
                    exc_info=True,
                )
