"""WORKFLOW (sub-workflow) spec.

Canonical mapping key is ``input_mapping`` — the legacy config key
``input_binding`` collided with the engine-level
``WorkflowNode.input_binding`` field. The form keeps the array-of-rows
widget; ``store_as="dict"`` folds rows into the ``{param: expression}``
dict at split time, so the handler always sees a dict.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping

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

WORKFLOW_SPEC = register(NodeSpec(
    node_type=NodeType.WORKFLOW,
    name="Workflow",
    description="Invokes another workflow as a sub-workflow.",
    category="control_flow.composition",
    runtime="engine",
    node_class=NodeClass.TECHNICAL,
    icon="Workflow",
    color="#6366F1",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(
        Port(id="success", label="Success"),
        Port(id="failure", label="Failure"),
    ),
    config_fields=(
        ConfigField(
            key="workflow_id", type=FieldType.STRING, label="Workflow ID",
            required=True,
            # Searchable workflow dropdown + "open sub-workflow" button,
            # rendered by the FE WorkflowPickerControl (placeholder is owned
            # by that control).
            field_control="WorkflowPickerControl",
        ),
        ConfigField(
            key="environment_id", type=FieldType.STRING, label="Environment",
            # SA-2365: bind the CHILD sub-workflow run to a specific environment
            # (its $env). Empty = inherit the parent run's environment. Rendered as a
            # searchable environment dropdown by the FE EnvironmentPickerControl.
            field_control="EnvironmentPickerControl",
        ),
        ConfigField(
            key="input_mapping", type=FieldType.OBJECT, label="Input Mapping",
            widget=Widget.ARRAY_TABLE, store_as="dict", expression=True,
            item_fields=(
                ConfigField(key="param_name", type=FieldType.STRING,
                            label="Parameter", placeholder="param1"),
                ConfigField(key="param_value", type=FieldType.STRING,
                            label="Value Expression",
                            placeholder="$.data.value", expression=True),
            ),
        ),
        ConfigField(
            key="wait_for_completion", type=FieldType.BOOLEAN,
            label="Wait for Completion", default=True, form_default=True,
        ),
        ConfigField(
            key="timeout_seconds", type=FieldType.NUMBER,
            label="Timeout (seconds)", placeholder="3600",
            default=3600, form_default=3600,
        ),
        # Engine-consumed knobs, API-authored only.
        ConfigField(key="timeout_ms", type=FieldType.NUMBER,
                    label="Timeout (ms)", internal=True),
        ConfigField(key="max_depth", type=FieldType.NUMBER,
                    label="Max Nesting Depth", default=10, internal=True),
        ConfigField(key="on_failure", type=FieldType.STRING,
                    label="On Failure", default="fail", internal=True),
        ConfigField(key="output_binding", type=FieldType.OBJECT,
                    label="Output Binding", internal=True),
    ),
    output_fields=(
        OutputField("sub_run_id", FieldType.STRING, "Sub-Run ID"),
        OutputField("sub_workflow_status", FieldType.STRING, "Sub-Workflow Status"),
        OutputField("output_data", FieldType.OBJECT, "Output Data"),
    ),
))


@dataclass(frozen=True)
class WorkflowNodeConfig:
    workflow_id: str = ""
    environment_id: str = ""  # SA-2365: child sub-workflow env ("" = inherit parent's)
    input_mapping: Mapping[str, Any] = field(default_factory=dict)
    wait_for_completion: bool = True
    timeout_seconds: int = 3600
    timeout_ms: int = 0
    max_depth: int = 10
    on_failure: str = "fail"
    output_binding: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_node(cls, node: Any) -> "WorkflowNodeConfig":
        return parse_config(WORKFLOW_SPEC, cls, node)
