"""APPROVAL_FLOW spec — fetch and visualize a change-request approval flow."""

from dataclasses import dataclass
from typing import Any

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

APPROVAL_FLOW_SPEC = register(NodeSpec(
    node_type=NodeType.APPROVAL_FLOW,
    name="Approval Flow",
    description="Fetches approval process from governance API and transforms to graph.",
    category="control_flow.integration",
    runtime="engine",
    node_class=NodeClass.BUSINESS,
    icon="ShieldCheck",
    color="#0891B2",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(
        Port(id="success", label="Success"),
        Port(id="failure", label="Failure"),
    ),
    config_fields=(
        ConfigField(
            key="change_request_id", type=FieldType.STRING,
            label="Change Request ID",
            placeholder="{{$nodeId.output.change_request_id}}",
            required=True, expression=True,
        ),
        ConfigField(
            key="instruction", type=FieldType.STRING,
            label="Instruction",
            widget=Widget.TEXTAREA,
            placeholder="Describe what this node does and how its output should be used.",
            description="Free-text note describing the purpose and usage of this node.",
        ),
    ),
    output_fields=(
        OutputField("nodes", FieldType.OBJECT, "Approval Nodes"),
        OutputField("edges", FieldType.OBJECT, "Approval Edges"),
        OutputField("change_request_id", FieldType.STRING, "Change Request ID"),
    ),
))


@dataclass(frozen=True)
class ApprovalFlowConfig:
    change_request_id: str = ""
    instruction: str = ""

    @classmethod
    def from_node(cls, node: Any) -> "ApprovalFlowConfig":
        return parse_config(APPROVAL_FLOW_SPEC, cls, node)
