"""Fan-out decision value objects for successor processing.

``FanoutDecision`` is the frozen result of routing a just-completed node's
outgoing edges to its successors; ``FanoutDecision.compute`` is the pure
factory that builds it. Intentionally independent of ``WorkflowRun``,
handlers, ``DataStoreService``, or any publisher — it works from a node's
outgoing edges + its completion status/port and returns a frozen decision.

Scope:
    1. Port filtering (success/failure for port-routed node types, or the
       handler-selected port via ``expected_port_override``).
    2. Deterministic event id generation for each fan-out action, so
       redeliveries dedupe at the event log.

The caller (the engine's successor dispatch / ``WorkflowRun.decide_successors``)
owns handler invocation, ``run.context`` mutation, and event emission.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from app.layer1_domain.entities.workflow import WorkflowEdge
from app.layer1_domain.value_objects.event_id import EventId
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus


# Node types with named success/failure output ports. Other BUILTIN types
# (TRIGGER, MERGE, LOOP, PARALLEL, CONDITION, SWITCH, INPUT, HUMAN_ACTION,
# DATA_EDIT, FILE_UPLOAD) do not participate in port filtering. CONDITION
# and SWITCH are handled by a separate branching-node path in the caller.
_PORT_ROUTED_TYPES = frozenset({
    NodeType.TASK,
    NodeType.COMPUTE,
    NodeType.WORKFLOW,
})


@dataclass(frozen=True)
class FanoutAction:
    """One outgoing edge whose target is ready to dispatch.

    The consumer emits an ``EdgeTraversed`` event carrying ``event_id``,
    then dispatches the target node.
    """
    event_id: str          # deterministic id for the EdgeTraversed event
    edge_id: str
    source_node_id: str
    target_node_id: str
    source_port_id: str    # filled from edge.source_port_id or the expected port fallback


@dataclass(frozen=True)
class SkipAction:
    """One node to skip via cascade-skip (untaken port subtree).

    The consumer emits a ``NodeCancelled`` event carrying ``event_id`` and
    adds ``node_id`` to ``run.context.skipped_nodes``.
    """
    event_id: str
    node_id: str
    reason: str  # "branch_not_taken" today — only cascade-skip reason the dispatcher produces


@dataclass(frozen=True)
class FanoutDecision:
    """Frozen result of evaluating a source node's downstream fan-out.

    Invariants:
    - ``actions`` targets are distinct from ``deferred_target_ids``.
    - ``deferred_target_ids`` are edge targets whose predecessors aren't
      all done yet; the consumer must leave them alone and wait for
      another predecessor path to complete.
    """
    actions: tuple[FanoutAction, ...]
    skipped: tuple[SkipAction, ...]
    deferred_target_ids: tuple[str, ...]
    expected_port: str  # "success" / "failure" / "" — fallback for EdgeTraversed.source_port_id

    @classmethod
    def compute(
        cls,
        *,
        causation_id: str,
        source_node_id: str,
        source_node_type: NodeType,
        source_task_status: TaskStatus,
        outgoing_edges: Sequence[WorkflowEdge],
        expected_port_override: str | None = None,
    ) -> "FanoutDecision":
        """Compute the downstream fan-out for a just-completed source node.

        Pure — identical inputs always produce identical output, byte-for-byte,
        because action event ids are generated via
        ``EventId.derive(causation_id, correlation_key)``.

        Args:
            causation_id: id of the upstream event that caused this fan-out
                (today the completed task id). Same causation across replays →
                same action event ids → event-log dedupe.
            source_node_id: node whose completion is being processed.
            source_node_type: used for port-routing decisions.
            source_task_status: COMPLETED → ``success`` port; otherwise ``failure``.
            outgoing_edges: the source node's outgoing edges (a flat sequence,
                or the full ``GraphTopology.edge_map`` dict — legacy callers).
            expected_port_override: when provided, bypasses the
                ``_PORT_ROUTED_TYPES`` check and uses this port directly for edge
                filtering (handler explicitly selected its output port). ``None``
                keeps the status-inferred behavior.
        """
        expected_port = ""
        # Accept either the flat list of outgoing edges for ``source_node_id``
        # (canonical call shape) or the full ``edge_map`` dict (legacy callers).
        if isinstance(outgoing_edges, dict):
            curr_node_outgoing_edges = outgoing_edges.get(source_node_id, [])
        else:
            curr_node_outgoing_edges = list(outgoing_edges)
        traversable: list[WorkflowEdge] = list(curr_node_outgoing_edges)

        # --- Port filtering ---------------------------------------------------
        # Edge-based dispatch: only edges that match the source's expected output
        # port (success/failure for port-routed nodes, or the port the handler
        # explicitly selected) are followed. Untaken edges are simply not emitted.
        if expected_port_override is not None:
            expected_port = expected_port_override
            traversable = [
                e for e in curr_node_outgoing_edges
                if not e.source_port_id or e.source_port_id == expected_port
            ]
        elif source_task_status != TaskStatus.COMPLETED:
            # A failed source routes via its ``failure`` port only — never its
            # success edges — regardless of node type. This is what makes
            # ``orchestrate_fail`` honour the failure edge (route it) or mark the
            # run FAILED when none exists (no traversable edge → no action).
            expected_port = "failure"
            traversable = [
                e for e in curr_node_outgoing_edges
                if not e.source_port_id or e.source_port_id == expected_port
            ]
        elif source_node_type in _PORT_ROUTED_TYPES:
            expected_port = "success"
            traversable = [
                e for e in curr_node_outgoing_edges
                if not e.source_port_id or e.source_port_id == expected_port
            ]

        # --- Fan-out actions --------------------------------------------------
        # Each traversable edge becomes a FanoutAction. The predecessor-readiness
        # AND-join gate is gone: a node fires when an edge to it is taken. MERGE
        # keeps its all-branches semantics inside the handler via
        # ``merge_quorum_buffers``; everything else is fire-on-first-arrival.
        actions: list[FanoutAction] = []
        for edge in traversable:
            target_id = edge.target_node_id
            # SA-846 cycle execution: a taken edge ALWAYS fires its target — an
            # already-completed target is a revisit, not a skip, and an
            # already-skipped target is un-skipped by the dispatch layer (which
            # discards the stale skip and bumps the node's visit counter so the
            # re-run gets a fresh task id).
            actions.append(
                FanoutAction(
                    event_id=EventId.derive(
                        causation_id, f"edge:{edge.id}:{target_id}",
                    ),
                    edge_id=edge.id,
                    source_node_id=edge.source_node_id,
                    target_node_id=target_id,
                    source_port_id=edge.source_port_id or expected_port,
                )
            )

        return cls(
            actions=tuple(actions),
            skipped=(),
            deferred_target_ids=(),
            expected_port=expected_port,
        )
