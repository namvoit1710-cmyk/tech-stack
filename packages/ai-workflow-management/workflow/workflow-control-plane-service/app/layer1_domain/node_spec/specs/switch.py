"""SWITCH spec — multi-way branching on rule rows or a legacy expression."""

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
from app.layer1_domain.node_spec.operators import COMPARISON_OPERATOR_CHOICES
from app.layer1_domain.node_spec.registry import register
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.port import Port

SWITCH_SPEC = register(NodeSpec(
    node_type=NodeType.SWITCH,
    name="Switch",
    description="Routes execution to one of several branches based on a value.",
    category="control_flow.branching",
    runtime="engine",
    node_class=NodeClass.TECHNICAL,
    icon="Shuffle",
    color="#6366F1",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(Port(id="default", label="Default"),),
    min_out_ports=1,
    requires_one_of=(("data", "expression"),),
    config_fields=(
        ConfigField(
            key="data", type=FieldType.ARRAY, label="Expression",
            widget=Widget.ARRAY_INLINE, expression=True,
            item_fields=(
                ConfigField(key="key", type=FieldType.STRING, label="Key",
                            placeholder="$.input", expression=True),
                ConfigField(key="operations", type=FieldType.STRING,
                            label="Operations",
                            choices=COMPARISON_OPERATOR_CHOICES),
                ConfigField(key="value", type=FieldType.STRING, label="Value",
                            placeholder="$", expression=True),
                ConfigField(key="branch", type=FieldType.STRING,
                            label="Branch name", placeholder="$"),
            ),
        ),
        # Legacy expression/cases triple — API-authored only. Liveness in
        # production data pending the T6.1 migration report (design §11.3);
        # dropped from the spec if zero occurrences.
        ConfigField(key="expression", type=FieldType.STRING,
                    label="Expression", expression=True, internal=True),
        ConfigField(key="cases", type=FieldType.ARRAY, label="Cases",
                    internal=True),
        ConfigField(key="default", type=FieldType.STRING, label="Default",
                    internal=True),
    ),
    output_fields=(
        OutputField("evaluated_value", FieldType.STRING, "Evaluated Value"),
        OutputField("matched_case", FieldType.STRING, "Matched Case"),
        OutputField("branch", FieldType.STRING, "Branch"),
    ),
))


@dataclass(frozen=True)
class SwitchNodeConfig:
    data: tuple[dict[str, Any], ...] = ()
    expression: str = ""
    cases: tuple[dict[str, Any], ...] = ()
    # None (not "") so "no default branch configured" keeps its legacy
    # falsy-vs-unset distinction in the handler's matched_target logic.
    default: Optional[str] = None

    @classmethod
    def from_node(cls, node: Any) -> "SwitchNodeConfig":
        return parse_config(SWITCH_SPEC, cls, node)
