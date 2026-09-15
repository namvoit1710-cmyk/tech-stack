"""Transformer for converting flat approval process data into graph structure.

Transforms the governance API response (flat list of approval steps) into
a graph with nodes and edges suitable for frontend visualization.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ApprovalNode:
    """A node in the approval process graph."""

    id: str
    role: str
    name: str | None
    status: str


@dataclass
class ApprovalEdge:
    """An edge connecting two nodes in the approval process graph."""

    source: str
    target: str


@dataclass
class ApprovalProcessGraph:
    """The complete approval process graph."""

    nodes: list[ApprovalNode]
    edges: list[ApprovalEdge]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "nodes": [
                {"id": n.id, "role": n.role, "name": n.name, "status": n.status}
                for n in self.nodes
            ],
            "edges": [{"source": e.source, "target": e.target} for e in self.edges],
        }


# Status mapping from governance API to display status
_STATUS_MAP = {
    "CONTRIBUTED": "Completed",
    "APPROVED": "Completed",
    "PENDING": "Processing",
    "INACTIVE": "Awaiting",
    "REJECTED": "Rejected",
    "CANCELLED": "Cancelled",
}

# Role mapping from governance API process type
_ROLE_MAP = {
    "CONTRIBUTE": "Contributor",
    "APPROVE": "Approver",
}


class ApprovalProcessTransformer:
    """Transforms flat approval process data into a graph structure."""

    def transform(self, approval_steps: list[dict[str, Any]]) -> ApprovalProcessGraph:
        """Transform flat approval steps into a graph with nodes and edges.

        Args:
            approval_steps: List of approval step dictionaries from governance API.

        Returns:
            ApprovalProcessGraph with nodes and edges for visualization.
        """
        if not approval_steps:
            return ApprovalProcessGraph(nodes=[], edges=[])

        nodes: list[ApprovalNode] = []
        edges: list[ApprovalEdge] = []

        # Build a lookup by STEP_ID for edge generation
        step_lookup: dict[int, dict[str, Any]] = {}
        for step in approval_steps:
            step_id = step.get("stepId")
            if step_id is not None:
                step_lookup[step_id] = step

        # Add start and end nodes
        nodes.append(ApprovalNode(id="start_node", role="Start", name=None, status="None"))

        # Process each step into a node
        for step in approval_steps:
            step_id = step.get("stepId")
            if step_id is None:
                continue

            node = self._create_node(step)
            nodes.append(node)

            # Steps with STEP_ORDER = 1 connect from start_node
            step_order = step.get("stepOrder", 0)
            if step_order == 1:
                edges.append(ApprovalEdge(source="start_node", target=str(step_id)))

            # Parse NEXT_STEPS to create edges to successor nodes
            next_steps_str = step.get("nextSteps")
            if next_steps_str:
                next_step_ids = self._parse_next_steps(next_steps_str)
                for next_id in next_step_ids:
                    edges.append(ApprovalEdge(source=str(step_id), target=str(next_id)))

        # Add end node
        nodes.append(ApprovalNode(id="end_node", role="End", name=None, status="None"))

        # Connect terminal nodes (no NEXT_STEPS) to end_node
        for step in approval_steps:
            step_id = step.get("stepId")
            next_steps_str = step.get("nextSteps")
            if step_id is not None and not next_steps_str:
                edges.append(ApprovalEdge(source=str(step_id), target="end_node"))

        return ApprovalProcessGraph(nodes=nodes, edges=edges)

    def _create_node(self, step: dict[str, Any]) -> ApprovalNode:
        """Create an ApprovalNode from a step dictionary."""
        step_id = str(step.get("stepId", ""))
        process_type = step.get("processType", "")
        status = step.get("status", "")
        assign_to = step.get("assignToId")

        return ApprovalNode(
            id=step_id,
            role=_ROLE_MAP.get(process_type, process_type),
            name=assign_to,
            status=_STATUS_MAP.get(status, status),
        )

    def _parse_next_steps(self, next_steps_str: str) -> list[int]:
        """Parse comma-separated NEXT_STEPS string into list of integers."""
        if not next_steps_str:
            return []

        result = []
        for part in next_steps_str.split(","):
            part = part.strip()
            if part.isdigit():
                result.append(int(part))
        return result
