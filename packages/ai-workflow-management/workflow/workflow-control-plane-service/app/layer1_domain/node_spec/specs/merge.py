"""MERGE spec — branch synchronization.

Canonical vocabulary is the handler's: ``all`` / ``any`` / ``n_of_m``.
The form labels keep the friendly names; the legacy stored values
(``wait_all``/``wait_any``/``wait_n``) and the duplicate keys
(``wait_strategy``, ``n_required``) are normalized away in Phase 6.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.layer1_domain.node_spec.model import (
    Choice,
    ConfigField,
    FieldType,
    NodeSpec,
    OutputField,
    VisibleWhen,
    parse_config,
)
from app.layer1_domain.node_spec.registry import register
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.port import Port

MERGE_SPEC = register(NodeSpec(
    node_type=NodeType.MERGE,
    name="Merge",
    description="Waits for all parallel branches to complete before continuing.",
    category="control_flow.concurrency",
    runtime="engine",
    node_class=NodeClass.TECHNICAL,
    icon="Merge",
    color="#10B981",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(Port(id="out_default", label="Output"),),
    min_out_ports=1,
    max_out_ports=1,
    config_fields=(
        ConfigField(
            key="merge_strategy", type=FieldType.STRING,
            label="Merge Strategy", required=True, default="all",
            choices=(Choice("all", "Wait All"),
                     Choice("any", "Wait Any"),
                     Choice("n_of_m", "Wait N")),
        ),
        ConfigField(
            key="wait_count", type=FieldType.NUMBER, label="Wait Count",
            placeholder="1",
            visible_when=VisibleWhen(field="merge_strategy", equals="n_of_m"),
        ),
        ConfigField(
            key="timeout_seconds", type=FieldType.NUMBER,
            label="Timeout (seconds)", placeholder="3600",
            default=3600, form_default=3600,
        ),
        ConfigField(
            key="on_branch_failure", type=FieldType.STRING,
            label="On Branch Failure", default="fail",
            choices=(Choice("fail", "Fail"), Choice("partial", "Partial")),
        ),
        # Engine-consumed: per-branch input contracts and fill-in defaults.
        ConfigField(
            key="inputs", type=FieldType.ARRAY, label="Inputs", internal=True,
            item_fields=(
                ConfigField(key="source", type=FieldType.STRING, label="Source"),
                ConfigField(key="key", type=FieldType.STRING, label="Key"),
                ConfigField(key="required", type=FieldType.BOOLEAN, label="Required"),
            ),
        ),
        ConfigField(key="defaults", type=FieldType.OBJECT, label="Defaults",
                    internal=True),
    ),
    output_fields=(
        OutputField("branches_received", FieldType.NUMBER, "Branches Received"),
        OutputField("completed", FieldType.BOOLEAN, "Completed"),
        OutputField("timed_out", FieldType.BOOLEAN, "Timed Out"),
    ),
))


@dataclass(frozen=True)
class MergeConfig:
    merge_strategy: str = "all"
    wait_count: int = 0
    timeout_seconds: int = 3600
    on_branch_failure: str = "fail"
    inputs: tuple[dict[str, Any], ...] = ()
    defaults: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_node(cls, node: Any) -> "MergeConfig":
        return parse_config(MERGE_SPEC, cls, node)
