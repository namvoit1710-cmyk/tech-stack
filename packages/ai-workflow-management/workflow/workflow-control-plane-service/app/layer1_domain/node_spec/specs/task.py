"""TASK spec — worker-backed execution.

Registers the registry's minimal TASK spec (config = ``input_types``
only; everything else in ``parameters`` is a runtime binding). Richer
per-worker specs are built at runtime by the Phase 5 NodeSpecResolver.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from app.layer1_domain.node_spec.model import parse_config
from app.layer1_domain.node_spec.registry import _MINIMAL_TASK_SPEC, register

TASK_SPEC = register(_MINIMAL_TASK_SPEC)


@dataclass(frozen=True)
class TaskConfig:
    input_types: Optional[Mapping[str, str]] = None

    @classmethod
    def from_node(cls, node: Any) -> "TaskConfig":
        return parse_config(TASK_SPEC, cls, node)
