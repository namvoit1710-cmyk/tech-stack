from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


class WorkflowValidationError(ValueError):
    """Raised when a proposed workflow definition violates a domain rule."""


def _as_float(value: object) -> float | None:
    """Coerce a raw x/y coordinate to float, or None when absent/invalid."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


@dataclass
class WorkflowNode:
    """A single node in a workflow graph."""

    id: str
    type: str
    name: str
    config: dict[str, object] = field(default_factory=dict)
    x: float | None = None
    y: float | None = None

    @classmethod
    def from_raw(cls, raw: object) -> "WorkflowNode":
        if not isinstance(raw, dict):
            raise WorkflowValidationError("Each node must be an object.")
        node_id = raw.get("id")
        node_type = raw.get("type")
        name = raw.get("name")
        config = raw.get("config", {})
        if not isinstance(node_id, str) or not node_id.strip():
            raise WorkflowValidationError("Every node needs a non-empty string id.")
        if not isinstance(node_type, str) or not node_type.strip():
            raise WorkflowValidationError(f"Node '{node_id}' needs a non-empty type.")
        if not isinstance(name, str) or not name.strip():
            raise WorkflowValidationError(f"Node '{node_id}' needs a non-empty name.")
        if not isinstance(config, dict):
            raise WorkflowValidationError(f"Node '{node_id}' config must be an object.")
        return cls(
            id=node_id.strip(), type=node_type.strip(), name=name.strip(), config=config,
            x=_as_float(raw.get("x")), y=_as_float(raw.get("y")),
        )

    def to_document(self) -> dict[str, object]:
        # x/y carry the canvas layout so nodes don't stack at the origin; omit when unset.
        doc: dict[str, object] = {"id": self.id, "type": self.type, "name": self.name, "config": self.config}
        if self.x is not None:
            doc["x"] = self.x
        if self.y is not None:
            doc["y"] = self.y
        return doc


@dataclass
class WorkflowEdge:
    """A directed connection between two workflow nodes."""

    source: str
    target: str

    @classmethod
    def from_raw(cls, raw: object) -> "WorkflowEdge":
        if not isinstance(raw, dict):
            raise WorkflowValidationError("Each edge must be an object.")
        source = raw.get("source")
        target = raw.get("target")
        if not isinstance(source, str) or not source.strip():
            raise WorkflowValidationError("Every edge needs a non-empty source.")
        if not isinstance(target, str) or not target.strip():
            raise WorkflowValidationError("Every edge needs a non-empty target.")
        if source.strip() == target.strip():
            raise WorkflowValidationError(f"Edge cannot connect node '{source}' to itself.")
        return cls(source=source.strip(), target=target.strip())

    def to_document(self) -> dict[str, str]:
        return {"source": self.source, "target": self.target}


@dataclass
class WorkflowDefinition:
    """A complete, validated workflow: a connected graph of at least two nodes."""

    MIN_NODES: ClassVar[int] = 2

    name: str = ""
    description: str = ""
    nodes: list[WorkflowNode] = field(default_factory=list)
    edges: list[WorkflowEdge] = field(default_factory=list)

    @classmethod
    def from_raw(
        cls,
        name: object,
        description: object,
        nodes: object,
        edges: object,
    ) -> "WorkflowDefinition":
        """Narrow untyped tool input into a validated definition (raises on any violation)."""
        if not isinstance(name, str) or not name.strip():
            raise WorkflowValidationError("Workflow name is required.")
        description_text = description.strip() if isinstance(description, str) else ""
        if not isinstance(nodes, list):
            raise WorkflowValidationError("nodes must be a list.")
        if not isinstance(edges, list):
            raise WorkflowValidationError("edges must be a list.")
        definition = cls(
            name=name.strip(),
            description=description_text,
            nodes=[WorkflowNode.from_raw(node) for node in nodes],
            edges=[WorkflowEdge.from_raw(edge) for edge in edges],
        )
        definition.validate()
        return definition

    def validate(self) -> None:
        if len(self.nodes) < self.MIN_NODES:
            raise WorkflowValidationError(
                f"A workflow needs at least {self.MIN_NODES} nodes; got {len(self.nodes)}."
            )
        node_ids = [node.id for node in self.nodes]
        duplicates = sorted({node_id for node_id in node_ids if node_ids.count(node_id) > 1})
        if duplicates:
            raise WorkflowValidationError(f"Duplicate node ids: {duplicates}.")
        known_ids = set(node_ids)
        for edge in self.edges:
            if edge.source not in known_ids:
                raise WorkflowValidationError(f"Edge source '{edge.source}' is not a known node.")
            if edge.target not in known_ids:
                raise WorkflowValidationError(f"Edge target '{edge.target}' is not a known node.")
        connected = {edge.source for edge in self.edges} | {edge.target for edge in self.edges}
        isolated = sorted(known_ids - connected)
        if isolated:
            raise WorkflowValidationError(f"Nodes not connected by any edge: {isolated}.")

    def to_document(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "nodes": [node.to_document() for node in self.nodes],
            "edges": [edge.to_document() for edge in self.edges],
        }
