"""SET_VARIABLE spec — declare run-scoped variables from expressions."""

from dataclasses import dataclass
from typing import Any

from app.layer1_domain.node_spec.model import (
    Choice,
    ConfigField,
    FieldType,
    NodeSpec,
    VisibleWhen,
    Widget,
    parse_config,
)
from app.layer1_domain.node_spec.registry import register
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.port import Port

_TYPE_CHOICES = (
    Choice("string", "String"),
    Choice("number", "Number"),
    Choice("boolean", "Boolean"),
    Choice("object", "Object"),
    Choice("array", "Array"),
)

_ITEM_TYPE_CHOICES = (
    Choice("string", "String"),
    Choice("number", "Number"),
    Choice("boolean", "Boolean"),
    Choice("object", "Object"),
)

SET_VARIABLE_SPEC = register(NodeSpec(
    node_type=NodeType.SET_VARIABLE,
    name="Set Variable",
    description="Writes named values into the run's variables namespace, readable downstream as {{$variables.<name>}}.",
    category="control_flow.transform",
    runtime="engine",
    node_class=NodeClass.TECHNICAL,
    icon="Variable",
    color="#0EA5E9",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(Port(id="out_default", label="Output"),),
    config_fields=(
        ConfigField(
            key="variables", type=FieldType.ARRAY, label="Variables to set",
            item_fields=(
                ConfigField(key="name", type=FieldType.STRING, label="Name",
                            placeholder="userCount", required=True),
                ConfigField(key="type", type=FieldType.STRING, label="Type",
                            choices=_TYPE_CHOICES, required=True),
                ConfigField(key="item_type", type=FieldType.STRING,
                            label="Item Type", choices=_ITEM_TYPE_CHOICES,
                            form_default="string",
                            visible_when=VisibleWhen(field="type",
                                                     equals="array")),
                ConfigField(key="value_expression", type=FieldType.STRING,
                            label="Value (literal or expression)",
                            widget=Widget.TEXTAREA, expression=True,
                            placeholder='{{$nodeId.totalUsers}} or 42 or "active"'),
            ),
        ),
    ),
    output_fields=(),
))


@dataclass(frozen=True)
class SetVariableConfig:
    variables: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_node(cls, node: Any) -> "SetVariableConfig":
        return parse_config(SET_VARIABLE_SPEC, cls, node)
