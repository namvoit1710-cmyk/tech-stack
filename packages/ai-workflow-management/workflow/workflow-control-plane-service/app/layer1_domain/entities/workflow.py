from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid

from app.layer1_domain.value_objects.workflow_status import WorkflowStatus
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.port import Port
from app.layer1_domain.value_objects.agent_ref import AgentRef
from app.layer1_domain.entities.base_entity import BaseDomainEntity
from app.layer1_domain.entities.base_audit_entity import BaseAuditDomainEntity


def normalize_schema(raw: Any) -> list[dict[str, Any]]:
    """Normalize schema to auto-form field list format. Handles legacy data."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        if not raw:
            return []
        if "fields" in raw and isinstance(raw["fields"], list):
            return raw["fields"]
        return []
    return []


# The parameters → (config, input_binding) split and its inverse live in
# app.layer1_domain.node_spec.parameter_mapper, derived from the per-type
# NodeSpec (single source of truth — see docs/design/node-spec-single-source.md).


@dataclass(kw_only=True)
class WorkflowNode(BaseDomainEntity):
    id: str | None = None
    workflow_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    node_type: NodeType = NodeType.TASK
    worker_type: str | None = None
    # Pin to a specific contract version of the referenced worker or
    # node_definition. None = unversioned → resolver picks latest non-deprecated.
    spec_version: str | None = None
    # --- frontend-facing unified field ---
    parameters: dict[str, Any] = field(default_factory=dict)
    # --- engine-internal fields (populated from parameters) ---
    input_binding: dict[str, Any] = field(default_factory=dict)
    output_binding: dict[str, Any] = field(default_factory=dict)
    input_require: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    x: float | None = None
    y: float | None = None
    node_class: str = ""
    description: str = ""
    action: str = ""
    instruction: str = ""
    agents: list[AgentRef] = field(default_factory=list)
    icon: str = ""
    color: str = ""
    tags: list[str] = field(default_factory=list)
    ports: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {"in": [], "out": []})
    in_ports: list[dict[str, Any]] = field(default_factory=list)
    out_ports: list[dict[str, Any]] = field(default_factory=list)
    node_definition_id: str | None = None
    editable: bool = True
    # Resolution artifact stamped at save time by the node-definition
    # enrichment step (NodeSpec Phase 4 — replaces the smuggled
    # ``config["_node_definition"]`` / ``config["_node_internals"]``).
    # Shape: {input_schema, output_schema, data_mode, edit_mode,
    # locked_defaults, input_mapping, output_mapping, base_worker_id}.
    # NOT part of ``parameters``; never round-trips to the FE.
    definition_snapshot: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.workflow_id:
            if not self.id or self.id is None:
                self.id = self.workflow_id
        if isinstance(self.node_type, str):
            self.node_type = NodeType(self.node_type)
        if isinstance(self.input_require, str):
            self.input_require = [k.strip() for k in self.input_require.split(",") if k.strip()]
        # Convert raw dicts to AgentRef instances for the agents field.
        # This enables deserialization from JSON where agents are stored as dicts.
        if self.agents:
            self.agents = [
                AgentRef.from_dict(a) if isinstance(a, dict) else a
                for a in self.agents
            ]
        # Split frontend `parameters` into engine `config` + `input_binding`
        # when neither is explicitly provided (i.e. new API format).
        # Spec-driven since Phase 2 of the NodeSpec redesign: which keys
        # are config (and how they coerce) comes from the node's spec.
        if self.parameters and not self.config and not self.input_binding:
            from app.layer1_domain.node_spec import get_spec
            from app.layer1_domain.node_spec.parameter_mapper import (
                split_parameters as _spec_split,
            )
            self.config, self.input_binding = _spec_split(
                get_spec(self.node_type), self.parameters,
            )
        # Normalize ports: accept both `ports: {in, out}` and legacy `in_ports/out_ports`.
        # `ports` is the canonical API format; `in_ports`/`out_ports` are internal storage.
        if self.ports and (self.ports.get("in") or self.ports.get("out")):
            if not self.in_ports:
                self.in_ports = self.ports.get("in", [])
            if not self.out_ports:
                self.out_ports = self.ports.get("out", [])
        self.ports = {"in": self.in_ports, "out": self.out_ports}


@dataclass(kw_only=True)
class WorkflowEdge(BaseDomainEntity):
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_node_id: str = ""
    target_node_id: str = ""
    condition: str | None = None
    source_port_id: str = ""
    target_port_id: str = ""
    label: str = ""
    description: str = ""

    def is_loop_exit(self) -> bool:
        """True if this is one of a loop's exit edges (leaves the body)."""
        cond = (self.condition or "").lower()
        port = (self.source_port_id or "").lower()
        return cond in ("exit", "complete") or port in (
            "loop_complete", "exit", "complete",
        )

    def is_loop_body(self) -> bool:
        """True if this edge is explicitly marked as a loop's body edge."""
        cond = (self.condition or "").lower()
        port = (self.source_port_id or "").lower()
        return cond == "body" or port in ("loop_body", "body")


@dataclass(kw_only=True)
class Workflow(BaseAuditDomainEntity):
    id: str | None = None
    workflow_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    version: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str = ""
    status: WorkflowStatus = WorkflowStatus.DRAFT
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    output_schema: list[dict[str, Any]] = field(default_factory=list)
    nodes: list[WorkflowNode] = field(default_factory=list)
    edges: list[WorkflowEdge] = field(default_factory=list)
    routing_path: str = ""
    purpose: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: str = "1.0.0"
    test_run_id: str | None = None
    main_flow: bool = False
    action: str = ""
    instruction: str = ""
    agents: list[AgentRef] = field(default_factory=list)
    # Author-defined custom variables exposed to expressions as
    # ``{{$variable.X}}``. Defaults baked into the workflow definition
    # at design time; per-run overrides live on
    # ``WorkflowRun.user_variables``. The effective view (defaults
    # overridden by run overrides) is assembled by
    # ``ResolutionScopeBuilder``.
    variables: dict[str, Any] = field(default_factory=dict)
    # SA-2570: reassignable owner (Keycloak ``sub``). Defaults to the creator
    # (``created_by``) on create; changed via PUT /workflows/{id}/owner. The
    # creator lives on ``created_by`` (inherited from BaseAuditDomainEntity).
    owner: str | None = None

    def __post_init__(self) -> None:
        """Post-initialization processing for type coercion."""
        # Convert raw dicts to AgentRef instances for the agents field.
        if self.workflow_id:
            if not self.id or self.id is None:
                self.id = self.workflow_id

        if self.agents:
            self.agents = [
                AgentRef.from_dict(a) if isinstance(a, dict) else a
                for a in self.agents
            ]

    def get_node(self, node_id: str) -> WorkflowNode | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def get_outgoing_edges(self, node_id: str) -> list[WorkflowEdge]:
        return [e for e in self.edges if e.source_node_id == node_id]

    def get_incoming_edges(self, node_id: str) -> list[WorkflowEdge]:
        return [e for e in self.edges if e.target_node_id == node_id]

    @classmethod
    def create(cls, name: str, description: str = "", **kwargs: Any) -> "Workflow":
        if not name or not name.strip():
            raise ValueError("Workflow name must not be empty")
        return cls(name=name.strip(), description=description.strip(), **kwargs)

    # ------------------------------------------------------------------
    # Snapshot serialization (used by WorkflowSnapshotRepository so a run
    # executes against a frozen definition immune to subsequent edits).
    # ------------------------------------------------------------------
    def to_snapshot_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representing this workflow.

        Round-trips with ``from_snapshot_dict``. Enums become their string
        ``.value``; ``AgentRef`` instances become dicts via ``to_dict``.
        Author and runtime fields are preserved as-is.
        """
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "input_schema": list(self.input_schema),
            "output_schema": list(self.output_schema),
            "nodes": [_node_to_snapshot(n) for n in self.nodes],
            "edges": [_edge_to_snapshot(e) for e in self.edges],
            "routing_path": self.routing_path,
            "purpose": self.purpose,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "schema_version": self.schema_version,
            "test_run_id": self.test_run_id,
            "main_flow": self.main_flow,
            "action": self.action,
            "instruction": self.instruction,
            "agents": [
                a.to_dict() if hasattr(a, "to_dict") else a
                for a in self.agents
            ],
            "variables": dict(self.variables),
        }

    @classmethod
    def from_snapshot_dict(cls, data: dict[str, Any]) -> "Workflow":
        """Reconstruct a ``Workflow`` from a ``to_snapshot_dict`` payload.

        Tolerant to missing optional keys (dataclass defaults fill in).
        ``WorkflowNode.__post_init__`` handles string→enum coercion and
        dict→AgentRef conversion, so passing raw dicts is safe.
        """
        d = dict(data)
        d["nodes"] = [
            WorkflowNode(**dict(n)) for n in d.get("nodes", [])
        ]
        d["edges"] = [
            WorkflowEdge(**dict(e)) for e in d.get("edges", [])
        ]
        return cls(**d)


def _node_to_snapshot(node: WorkflowNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "name": node.name,
        "node_type": node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
        "worker_type": node.worker_type,
        "spec_version": node.spec_version,
        "parameters": dict(node.parameters),
        "input_binding": dict(node.input_binding),
        "output_binding": dict(node.output_binding),
        "input_require": list(node.input_require),
        "config": dict(node.config),
        "x": node.x,
        "y": node.y,
        "node_class": node.node_class,
        "description": node.description,
        "action": node.action,
        "instruction": node.instruction,
        "agents": [
            a.to_dict() if hasattr(a, "to_dict") else a
            for a in node.agents
        ],
        "icon": node.icon,
        "color": node.color,
        "tags": list(node.tags),
        "ports": {k: list(v) for k, v in (node.ports or {}).items()},
        "in_ports": list(node.in_ports),
        "out_ports": list(node.out_ports),
        "node_definition_id": node.node_definition_id,
        "editable": node.editable,
        "definition_snapshot": node.definition_snapshot,
    }


def _edge_to_snapshot(edge: WorkflowEdge) -> dict[str, Any]:
    return {
        "id": edge.id,
        "source_node_id": edge.source_node_id,
        "target_node_id": edge.target_node_id,
        "condition": edge.condition,
        "source_port_id": edge.source_port_id,
        "target_port_id": edge.target_port_id,
        "label": edge.label,
        "description": edge.description,
    }
