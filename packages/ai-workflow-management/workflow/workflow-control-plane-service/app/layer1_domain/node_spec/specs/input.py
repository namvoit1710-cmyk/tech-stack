"""INPUT spec — pauses the run to collect structured user input."""

from dataclasses import dataclass
from typing import Any, Mapping, Optional

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

_FULL_SPAN = "xl-12 lg-12 md-12 xs-12"

INPUT_SPEC = register(NodeSpec(
    node_type=NodeType.INPUT,
    name="Input",
    description="Pauses workflow to collect structured input from a user.",
    category="human_in_the_loop.input",
    runtime="pending",
    node_class=NodeClass.TECHNICAL,
    icon="FormInput",
    color="#8B5CF6",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(Port(id="out_default", label="Output"),),
    config_fields=(
        ConfigField(
            key="instruction", type=FieldType.STRING, label="Instruction",
            widget=Widget.TEXTAREA,
            placeholder="Please fill in the required information.",
        ),
        ConfigField(
            key="input_schema", type=FieldType.OBJECT, label="Input Schema",
            field_control="FormBuilderControl",
            render_props={"labelSpan": _FULL_SPAN, "fieldSpan": _FULL_SPAN},
        ),
        # Engine-consumed (no form field today).
        ConfigField(key="input_types", type=FieldType.OBJECT,
                    label="Input Types", internal=True),
        ConfigField(key="timeout_minutes", type=FieldType.NUMBER,
                    label="Timeout (minutes)", internal=True),
        ConfigField(key="file_id", type=FieldType.STRING, label="File ID",
                    expression=True, internal=True),
        ConfigField(key="dynamic_table", type=FieldType.BOOLEAN,
                    label="Dynamic Table", internal=True),
        ConfigField(key="runThrough", type=FieldType.BOOLEAN,
                    label="Run Through", internal=True),
    ),
    output_fields=(
        OutputField("submitted_data", FieldType.OBJECT, "Submitted Data"),
    ),
))


@dataclass(frozen=True)
class InputNodeConfig:
    instruction: str = ""
    input_schema: Any = None
    input_types: Optional[Mapping[str, str]] = None
    timeout_minutes: Optional[int] = None
    file_id: str = ""
    dynamic_table: bool = False
    runThrough: bool = False  # noqa: N815 — canonical stored key is camelCase

    @classmethod
    def from_node(cls, node: Any) -> "InputNodeConfig":
        return parse_config(INPUT_SPEC, cls, node)
