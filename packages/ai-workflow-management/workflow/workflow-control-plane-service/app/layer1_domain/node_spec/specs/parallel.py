"""PARALLEL spec — concurrent branch fan-out.

The legacy form's ``max_concurrency``/``fail_strategy`` fields were read
by nobody and are dropped (design §5); declare them here only when the
handler implements them. ``branches`` is engine-consumed.
"""

from dataclasses import dataclass
from typing import Any

from app.layer1_domain.node_spec.model import (
    ConfigField,
    FieldType,
    NodeSpec,
    OutputField,
    parse_config,
)
from app.layer1_domain.node_spec.registry import register
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.port import Port

PARALLEL_SPEC = register(NodeSpec(
    node_type=NodeType.PARALLEL,
    name="Parallel",
    description="Executes multiple branches concurrently.",
    category="control_flow.concurrency",
    runtime="engine",
    node_class=NodeClass.TECHNICAL,
    icon="LayoutGrid",
    color="#0EA5E9",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(),
    min_out_ports=1,
    config_fields=(
        ConfigField(key="branches", type=FieldType.ARRAY, label="Branches",
                    internal=True),
    ),
    output_fields=(
        OutputField("branches_total", FieldType.NUMBER, "Branches Total"),
        OutputField("branches_completed", FieldType.NUMBER, "Branches Completed"),
        OutputField("branches_failed", FieldType.NUMBER, "Branches Failed"),
        OutputField("all_succeeded", FieldType.BOOLEAN, "All Succeeded"),
    ),
))


@dataclass(frozen=True)
class ParallelConfig:
    branches: tuple[Any, ...] = ()

    @classmethod
    def from_node(cls, node: Any) -> "ParallelConfig":
        return parse_config(PARALLEL_SPEC, cls, node)
