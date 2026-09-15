"""Successor processing: advance the DAG after each task completion."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.layer1_domain.node_spec.model import NodeRuntime
from app.layer1_domain.node_spec.registry import node_types_for_runtime
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.events import (
    DomainEvent,
    EdgeTraversed,
    NodeOutputProduced,
    NodeDispatched,
    NodeFailed,
)
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.value_objects.event_id import EventId
from app.layer2_application.node_handlers.base import (
    NodeHandlerRegistry,
    NodeHandlerResult,
    invoke_handler,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer1_domain.exceptions import ExpressionError, InputRequirementError, MaxDepthExceededError
from app.layer1_domain.fanout import FanoutDecision

# Node types that have success/failure output ports requiring port-based routing.
_PORT_ROUTED_TYPES = frozenset({
    NodeType.TASK,
    NodeType.COMPUTE,
    NodeType.WORKFLOW,
    NodeType.APPROVAL_FLOW,
})

# Node types whose handlers perform their own edge routing (true/false, cases)
# via ``taken_edge_ports`` instead of ``output_port`` + ``compute_fanout``.
# ``OrchestrationEngine._route_to_successors`` keys its routing mode off this
# set: a branching node follows ONLY its selected edges, and an empty selection
# dispatches nothing. An unwired chosen branch must NOT fall through to the
# generic edge loop, which would blindly traverse ALL outgoing edges and fire
# the wrong one.
BRANCHING_NODE_TYPES = frozenset({NodeType.CONDITION, NodeType.SWITCH})


# Inline ("builtin") and pending node-type sets are DERIVED from the domain
# NodeSpec registry, not hand-listed: a node type is inline iff its spec
# declares ``runtime="engine"`` and pending iff ``runtime="pending"``
# (see ``node_spec.registry.node_types_for_runtime`` / ``NodeRuntime``).
# Registering a new inline node's spec with ``runtime="engine"`` auto-includes
# it here — there is no second list to update, which is how PROMPT and OUTPUT
# previously drifted out of the inline path and got mis-routed to the worker
# dispatch branch. ``tests/unit/node_spec/test_runtime_classification.py``
# guards that the handler ``shape`` and the spec ``runtime`` never disagree.
BUILTIN_NODE_TYPES = node_types_for_runtime(NodeRuntime.ENGINE)

PENDING_NODE_TYPES = node_types_for_runtime(NodeRuntime.PENDING)

MAX_RECURSION_DEPTH = 100


def discard_stale_node_variable(run: WorkflowRun, node: WorkflowNode) -> None:
    """SA-846 revisit: drop the re-run node's previous-visit aggregate
    ``variables`` entry (and its name alias).

    The view→aggregate hydration uses ``setdefault`` (deliberately — it
    must not clobber aggregate-owned keys like ``input``), so a stale
    per-node entry written on the FIRST visit was never overwritten on a
    revisit: a loop CONDITION around a sub-workflow always resolved
    iteration 1's child output and spun forever. Discarding the entry at
    every visit-counter bump lets the next hydration refill it from the
    authoritative projector view.
    """
    run.context.variables.pop(node.id, None)
    if node.name and node.name != node.id:
        run.context.variables.pop(node.name, None)


@dataclass
class ProcessingResult:
    tasks_to_dispatch: list[Task] = field(default_factory=list)
    skipped_node_ids: list[str] = field(default_factory=list)
    state_updates: dict = field(default_factory=dict)
    loop_state_updates: dict = field(default_factory=dict)
    events: list[DomainEvent] = field(default_factory=list)

    def merge(self, handler_result: NodeHandlerResult) -> None:
        self.tasks_to_dispatch.extend(handler_result.tasks_to_dispatch)
        self.skipped_node_ids.extend(handler_result.skipped_node_ids)
        self.state_updates.update(handler_result.state_updates)
        if handler_result.loop_state_updates:
            self.loop_state_updates.update(handler_result.loop_state_updates)
        self.events.extend(handler_result.events)


class SuccessorProcessor:

    def __init__(
        self,
        handler_registry: NodeHandlerRegistry,
        task_factory: TaskFactory,
        data_store_service: object | None = None,
        monitor: object | None = None,
        run_context_view_store: object | None = None,
    ) -> None:
        self._handlers = handler_registry
        self._task_factory = task_factory
        self._data_store_service = data_store_service
        self._monitor = monitor
        self._view_store = run_context_view_store

    async def process(
        self,
        run: WorkflowRun,
        workflow: Workflow,
        decision: FanoutDecision,
        depth: int = 0
    ) -> ProcessingResult:
        if depth > MAX_RECURSION_DEPTH:
            raise MaxDepthExceededError(
                f"Successor processing exceeded max recursion depth ({MAX_RECURSION_DEPTH})"
            )
        # Phase 6.3: all processing goes through the uniform path.
        return await self._process_uniform(run, workflow, decision)

    def _create_failed_task(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        exc: Exception,
        results: ProcessingResult,
    ) -> Task:
        """Create a FAILED task for a node whose handler raised an error."""
        now = datetime.now(timezone.utc).isoformat()
        failed_task = self._task_factory.create_task(node, run)
        failed_task.status = TaskStatus.FAILED
        failed_task.error = str(exc)
        failed_task.completed_at = now
        results.tasks_to_dispatch.append(failed_task)
        run.context.completed_nodes.add(node.id)
        results.events.append(
            NodeFailed(
                task_id=failed_task.id,
                run_id=run.id,
                workflow_id=run.workflow_id,
                node_id=node.id,
                error_code=type(exc).__name__,
                error_message=str(exc),
                attempt=0,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
            )
        )
        return failed_task

    @staticmethod
    def _is_builtin_node(node_id: str, workflow: Workflow) -> bool:
        node = workflow.get_node(node_id)
        return node is not None and node.node_type in BUILTIN_NODE_TYPES

    @staticmethod
    def _loop_has_body(node: Any, run: WorkflowRun) -> bool:
        """Return True if the loop node has items to iterate over."""
        loop_state = run.context.loop_state.get(node.id)
        return bool(loop_state and loop_state.get("total", 0) > 0)

    async def _process_uniform(
        self,
        run: WorkflowRun,
        workflow: Workflow,
        decision: Any
    ) -> ProcessingResult:
        """Phase 6.2 — simplified successor processing for uniform mode.

        Computes the fan-out (edge filtering + cascade-skip) and returns
        READY tasks for the caller to enqueue. Does NOT invoke handlers,
        does NOT recurse into BUILTIN successors, does NOT emit
        NodeDispatched/NodeCompleted/NodeOutputProduced — those are all
        handled by the engine's ``_exec_uniform`` when the
        ``ExecuteNodeAction`` is dequeued.
        """
        results = ProcessingResult()

        # Edge-based dispatch: ``decision.skipped`` is always empty (the
        # downstream-skip BFS has been retired). SA-846: a taken edge
        # ALWAYS runs its target — an already-completed target is a
        # revisit (cycle re-entry or late second arrival), and an
        # already-skipped target is un-skipped: every skip is
        # flow-derived (branch_not_taken / child-fail), so a later taken
        # edge legitimately overrides it. Either way the node's visit
        # counter is bumped so ``create_task`` mints a fresh ``-v{n}``
        # task id. The discard/bump is keyed on the AGGREGATE skip set
        # (not the projector view): the aggregate updates synchronously,
        # so a multi-predecessor node (MERGE) un-skips and bumps exactly
        # once per revisit — subsequent arrivals resolve to the same
        # task id and upsert (the quorum-accumulation contract).
        view = self._view_store.get(run.id) if self._view_store else None
        completed_set: set[str] = set(run.context.completed_nodes)
        if view is not None:
            completed_set |= view.completed_nodes

        for action in decision.actions:
            target_node = workflow.get_node(action.target_node_id)
            if not target_node:
                continue
            revisit = target_node.id in completed_set
            if target_node.id in run.context.skipped_nodes:
                run.context.skipped_nodes.discard(target_node.id)
                revisit = True
            if revisit:
                visits = run.context.node_visits
                visits[target_node.id] = visits.get(target_node.id, 0) + 1
                discard_stale_node_variable(run, target_node)
            results.events.append(
                EdgeTraversed(
                    event_id=action.event_id,
                    run_id=run.id,
                    workflow_id=run.workflow_id,
                    edge_id=action.edge_id,
                    source_node_id=action.source_node_id,
                    target_node_id=action.target_node_id,
                    source_port_id=action.source_port_id,
                    root_run_id=run.root_run_id or run.id,
                    depth=run.depth_level,
                    source="workflow-control-plane",
                )
            )
            task = self._task_factory.create_task(target_node, run)
            results.tasks_to_dispatch.append(task)

        return results

    # _evaluate_branching_node REMOVED in Phase 6.3 —
    # CONDITION/SWITCH are now handled by _exec_uniform.
