"""Builtin spec registry — keyed by NodeType, populated by ``specs/`` modules.

Replaces ``node_type_registry.NodeTypeDescriptor`` (deleted in Phase 5):
ports/icon/color/node_class now live on the spec itself.
"""

from __future__ import annotations

from app.layer1_domain.node_spec.model import (
    ConfigField,
    FieldType,
    NodeRuntime,
    NodeSpec,
)
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.port import Port

BUILTIN_SPECS: dict[NodeType, NodeSpec] = {}


def register(spec: NodeSpec) -> NodeSpec:
    if spec.node_type in BUILTIN_SPECS:
        raise ValueError(f"duplicate spec registration for {spec.node_type}")
    BUILTIN_SPECS[spec.node_type] = spec
    return spec


# Minimal fallback for node types without a richer spec: TASK and any
# worker-backed node. Config side is ``input_types`` only — everything
# else in ``parameters`` flows to ``input_binding`` (today's behavior).
_MINIMAL_TASK_SPEC = NodeSpec(
    node_type=NodeType.TASK,
    name="Task",
    description="Executes a task via a registered worker.",
    category="worker",
    runtime="worker",
    node_class=NodeClass.BUSINESS,
    icon="Cog",
    color="#3B82F6",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(
        Port(id="success", label="Success"),
        Port(id="failure", label="Failure"),
    ),
    config_fields=(
        ConfigField(
            key="input_types", type=FieldType.OBJECT,
            label="Input Types", internal=True,
            description="key -> declared type map applied to resolved input",
        ),
    ),
)


def get_spec(node_type: NodeType) -> NodeSpec:
    """Spec for a node type; unknown/worker types get the minimal TASK spec."""
    return BUILTIN_SPECS.get(node_type, _MINIMAL_TASK_SPEC)


def _ensure_specs_loaded() -> None:
    """Import the specs package so module-level ``register`` calls run."""
    from app.layer1_domain.node_spec import specs as _specs  # noqa: F401


def node_types_for_runtime(runtime: NodeRuntime | str) -> frozenset[NodeType]:
    """All registered node types whose spec declares the given ``runtime``.

    This is the single source of truth for the engine's inline / pending /
    worker classification: a node type is "builtin/inline" iff its spec
    declares ``runtime="engine"``, "pending" iff ``runtime="pending"``, and
    so on. Adding a new node type only requires registering its spec with the
    right ``runtime`` — the orchestrator's ``BUILTIN_NODE_TYPES`` /
    ``PENDING_NODE_TYPES`` are derived from this and pick it up automatically.
    No hand-maintained list to keep in sync (which is how PROMPT and OUTPUT
    were silently mis-routed to the worker-dispatch path).
    """
    _ensure_specs_loaded()
    target = runtime.value if isinstance(runtime, NodeRuntime) else str(runtime)
    return frozenset(
        node_type
        for node_type, spec in BUILTIN_SPECS.items()
        if spec.runtime == target
    )
