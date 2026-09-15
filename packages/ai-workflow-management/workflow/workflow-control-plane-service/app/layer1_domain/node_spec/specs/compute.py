"""COMPUTE spec — in-process expression evaluation."""

from dataclasses import dataclass
from typing import Any, Optional

from app.layer1_domain.node_spec.model import (
    ConfigField,
    FieldType,
    NodeSpec,
    OutputField,
    Widget,
    parse_config,
)
from app.layer1_domain.node_spec.registry import register
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.port import Port

COMPUTE_SPEC = register(NodeSpec(
    node_type=NodeType.COMPUTE,
    name="Compute",
    description="Performs in-process data transformation or computation.",
    category="control_flow.transform",
    runtime="engine",
    node_class=NodeClass.TECHNICAL,
    icon="Code2",
    color="#64748B",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(
        Port(id="success", label="Success"),
        Port(id="failure", label="Failure"),
    ),
    config_fields=(
        ConfigField(
            key="expression", type=FieldType.STRING, label="Expression",
            widget=Widget.TEXTAREA, required=True, expression=True,
            placeholder="len({{$nodeId.output.items}}) or {{$input.price}} * 1.08",
        ),
        ConfigField(
            key="result_key", type=FieldType.STRING, label="Result Key",
            placeholder="total",
        ),
    ),
    output_fields=(
        OutputField("result", FieldType.OBJECT, "Result"),
    ),
))


@dataclass(frozen=True)
class ComputeConfig:
    expression: str = ""
    result_key: Optional[str] = None

    @classmethod
    def from_node(cls, node: Any) -> "ComputeConfig":
        return parse_config(COMPUTE_SPEC, cls, node)
