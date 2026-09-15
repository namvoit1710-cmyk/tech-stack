"""CONDITION spec — boolean rule rows routed to true/false branches."""

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
from app.layer1_domain.node_spec.operators import COMPARISON_OPERATOR_CHOICES
from app.layer1_domain.node_spec.registry import register
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.port import Port

CONDITION_SPEC = register(NodeSpec(
    node_type=NodeType.CONDITION,
    name="Condition",
    description="Evaluates a boolean expression and routes to true/false branches.",
    category="control_flow.branching",
    runtime="engine",
    node_class=NodeClass.TECHNICAL,
    icon="GitBranch",
    color="#8B5CF6",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(
        Port(id="true_branch", label="True"),
        Port(id="false_branch", label="False"),
    ),
    requires_one_of=(("data", "expression"),),
    config_fields=(
        ConfigField(
            key="data", type=FieldType.ARRAY, label="Data",
            widget=Widget.ARRAY_INLINE, expression=True,
            item_fields=(
                ConfigField(key="key", type=FieldType.STRING, label="Key",
                            placeholder="$.input", expression=True),
                ConfigField(key="operations", type=FieldType.STRING,
                            label="Operations",
                            choices=COMPARISON_OPERATOR_CHOICES),
                ConfigField(key="value", type=FieldType.STRING, label="Value",
                            placeholder="$", expression=True),
            ),
        ),
        # Free-text boolean expression — API-authored alternative to the
        # structured rows; exactly one of the two may be set.
        ConfigField(key="expression", type=FieldType.STRING,
                    label="Expression", expression=True, internal=True),
    ),
    output_fields=(
        OutputField("result", FieldType.BOOLEAN, "Result"),
        OutputField("branch", FieldType.STRING, "Branch"),
    ),
))


@dataclass(frozen=True)
class ConditionNodeConfig:
    data: tuple[dict[str, Any], ...] = ()
    expression: str = ""

    @classmethod
    def from_node(cls, node: Any) -> "ConditionNodeConfig":
        return parse_config(CONDITION_SPEC, cls, node)
