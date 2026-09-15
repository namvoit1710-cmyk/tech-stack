"""HUMAN_ACTION spec — pause for a human to confirm or reject."""

from dataclasses import dataclass
from typing import Any, Mapping, Optional

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

HUMAN_ACTION_SPEC = register(NodeSpec(
    node_type=NodeType.HUMAN_ACTION,
    name="Human Action",
    description="Pauses workflow for a human to perform an action and confirm or reject.",
    category="human_in_the_loop.action",
    runtime="pending",
    node_class=NodeClass.TECHNICAL,
    icon="UserCheck",
    color="#F59E0B",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(
        Port(id="confirm", label="Confirm"),
        Port(id="reject", label="Reject"),
    ),
    config_fields=(
        ConfigField(
            key="instruction", type=FieldType.STRING, label="Instruction",
            widget=Widget.TEXTAREA, required=True,
            placeholder="Review the document and approve or reject.",
        ),
        ConfigField(
            key="input_schema", type=FieldType.ARRAY, label="Input Schema",
            widget=Widget.ARRAY_TABLE,
            item_fields=(
                ConfigField(key="key", type=FieldType.STRING, label="Key",
                            placeholder="comments"),
                ConfigField(key="type", type=FieldType.STRING, label="Type",
                            choices=(Choice("string", "String"),
                                     Choice("number", "Number"),
                                     Choice("boolean", "Boolean"),
                                     Choice("file", "File"))),
                ConfigField(key="label", type=FieldType.STRING, label="Label",
                            placeholder="Comments"),
                ConfigField(key="required", type=FieldType.BOOLEAN,
                            label="Required"),
            ),
        ),
        ConfigField(
            key="confirm_label", type=FieldType.STRING,
            label="Confirm Button Label", placeholder="Approve",
            default="Confirm", form_default="Confirm",
        ),
        ConfigField(
            key="reject_label", type=FieldType.STRING,
            label="Reject Button Label", placeholder="Reject",
            default="Reject", form_default="Reject",
        ),
        ConfigField(key="input_types", type=FieldType.OBJECT,
                    label="Input Types", internal=True),
    ),
    output_fields=(
        OutputField("action", FieldType.STRING, "Action"),
        OutputField("submitted_data", FieldType.OBJECT, "Submitted Data"),
    ),
))


@dataclass(frozen=True)
class HumanActionConfig:
    instruction: str = ""
    input_schema: tuple[dict[str, Any], ...] = ()
    confirm_label: str = "Confirm"
    reject_label: str = "Reject"
    input_types: Optional[Mapping[str, str]] = None

    @classmethod
    def from_node(cls, node: Any) -> "HumanActionConfig":
        return parse_config(HUMAN_ACTION_SPEC, cls, node)
