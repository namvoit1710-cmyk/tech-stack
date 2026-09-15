"""OUTPUT spec — declares the workflow's output contract."""

from dataclasses import dataclass
from typing import Any

from app.layer1_domain.node_spec.model import (
    Choice,
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

OUTPUT_SPEC = register(NodeSpec(
    node_type=NodeType.OUTPUT,
    name="Output",
    description="Defines and formats the output schema for workflow results.",
    category="control_flow.output",
    runtime="engine",
    node_class=NodeClass.TECHNICAL,
    icon="FileOutput",
    color="#10B981",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(Port(id="out_default", label="Output"),),
    config_fields=(
        ConfigField(
            key="instruction", type=FieldType.STRING, label="Instruction",
            widget=Widget.TEXTAREA,
            placeholder="Describe the workflow output format.",
        ),
        ConfigField(
            key="output_schema", type=FieldType.ARRAY, label="Output Schema",
            widget=Widget.ARRAY_INLINE, expression=True,
            item_fields=(
                ConfigField(key="key", type=FieldType.STRING, label="Key",
                            placeholder="field_name", required=True),
                ConfigField(key="type", type=FieldType.STRING, label="Type",
                            form_default="string",
                            choices=(Choice("string", "String"),
                                     Choice("number", "Number"),
                                     Choice("boolean", "Boolean"),
                                     Choice("object", "Object"),
                                     Choice("array", "Array"))),
                ConfigField(key="value", type=FieldType.STRING, label="Value",
                            placeholder="{{$nodeId.output.field}}",
                            expression=True),
            ),
        ),
    ),
    output_fields=(
        OutputField("formatted_output", FieldType.OBJECT, "Formatted Output"),
    ),
))


@dataclass(frozen=True)
class OutputNodeConfig:
    instruction: str = ""
    output_schema: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_node(cls, node: Any) -> "OutputNodeConfig":
        return parse_config(OUTPUT_SPEC, cls, node)
