"""Node type definition — describes a worker type that registers as a separate node."""

from dataclasses import dataclass, field
from typing import Any, Optional
from collections.abc import Callable, Coroutine

from worker_sdk.layer1_domain.entities.worker_function import WorkerFunction
from worker_sdk.layer1_domain.value_objects.node_kind import NodeKind


# Functional taxonomy (SA-1734/SA-1735). Per-layer copy by decision Q1; a
# parity test guards drift across worker-sdk / executor / control-plane / FE.
# Derived from NodeKind — nothing may re-declare the members.
VALID_NODE_KINDS = tuple(k.value for k in NodeKind)


@dataclass(kw_only=True)
class NodeTypeDefinition:
    """Defines a single node type within a multi-type worker.

    Each NodeTypeDefinition registers as a separate worker_type with the executor
    and appears as its own node in the workflow builder.
    """

    worker_type: str
    name: str = ""
    description: str = ""
    # Build identity — bump on any code change to this worker.
    version: str = "0.1.0"
    # Contract identity — bump only when input_schema / output_schema / ports
    # change in a way that could affect workflows referencing this node.
    spec_version: str = "1.0.0"
    node_class: str = "BUSINESS"
    kind: NodeKind = NodeKind.ACTION
    icon: str = "Cog"
    color: str = "#3B82F6"
    tags: list[str] = field(default_factory=list)
    capabilities: list[dict[str, str]] = field(default_factory=list)
    ports: Optional[dict[str, list[dict[str, Any]]]] = None
    input_schema: Optional[list[dict[str, Any]]] = None
    output_schema: Optional[list[dict[str, Any]]] = None
    functions: list[WorkerFunction] = field(default_factory=list)
    handler: Optional[Callable[..., Coroutine[Any, Any, dict[str, Any]]]] = None

    def __post_init__(self) -> None:
        if not self.worker_type or not self.worker_type.strip():
            raise ValueError(
                "NodeTypeDefinition.worker_type is required and cannot be blank"
            )
        try:
            self.kind = NodeKind(self.kind)
        except ValueError:
            raise ValueError(
                f"NodeTypeDefinition.kind {self.kind!r} must be one of {VALID_NODE_KINDS}"
            ) from None
