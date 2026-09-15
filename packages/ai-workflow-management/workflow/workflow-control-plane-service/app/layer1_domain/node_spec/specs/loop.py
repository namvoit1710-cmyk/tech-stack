"""LOOP spec — collection/count/condition iteration.

Canonical collection key is ``collection``; the ``items_expression``
alias read by the loop driver dies with the Phase 6 migration.
"""

from dataclasses import dataclass
from typing import Any

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

LOOP_SPEC = register(NodeSpec(
    node_type=NodeType.LOOP,
    name="Loop",
    description="Iterates over a collection or repeats until a condition is met.",
    category="control_flow.iteration",
    runtime="engine",
    node_class=NodeClass.TECHNICAL,
    icon="Repeat",
    color="#F97316",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(
        Port(id="loop_body", label="Loop Body"),
        Port(id="loop_complete", label="Loop Complete"),
    ),
    config_fields=(
        ConfigField(
            key="loop_type", type=FieldType.STRING, label="Loop Type",
            required=True, default="collection",
            choices=(Choice("collection", "Collection"),
                     Choice("count", "Count"),
                     Choice("condition", "Condition")),
        ),
        ConfigField(
            key="collection", type=FieldType.STRING, label="Collection",
            placeholder="{{$input.items}}", expression=True,
            visible_when=VisibleWhen(field="loop_type", equals="collection"),
        ),
        ConfigField(
            key="max_iterations", type=FieldType.NUMBER,
            label="Max Iterations", placeholder="100", default=10000,
        ),
        ConfigField(
            key="condition", type=FieldType.STRING, label="Condition",
            placeholder="{{$nodeA.hasMore}}", expression=True,
            visible_when=VisibleWhen(field="loop_type", equals="condition"),
        ),
        ConfigField(
            key="mode", type=FieldType.STRING, label="Execution Mode",
            default="sequential",
            choices=(Choice("sequential", "Sequential"),
                     Choice("parallel", "Parallel")),
        ),
        ConfigField(
            key="parallelism", type=FieldType.NUMBER, label="Max Parallelism",
            placeholder="5",
            visible_when=VisibleWhen(field="mode", equals="parallel"),
        ),
        ConfigField(
            key="counter_variable", type=FieldType.STRING,
            label="Counter Variable", placeholder="index",
            default="index", form_default="index",
        ),
    ),
    output_fields=(
        OutputField("iteration_count", FieldType.NUMBER, "Iteration Count"),
        OutputField("completed", FieldType.BOOLEAN, "Completed"),
        OutputField("break_reason", FieldType.STRING, "Break Reason"),
    ),
))


@dataclass(frozen=True)
class LoopConfig:
    loop_type: str = "collection"
    collection: str = ""
    max_iterations: int = 10000
    condition: str = ""
    mode: str = "sequential"
    parallelism: int = 0  # 0 = unbounded (driver caps at item count)
    counter_variable: str = "index"

    @classmethod
    def from_node(cls, node: Any) -> "LoopConfig":
        return parse_config(LOOP_SPEC, cls, node)
