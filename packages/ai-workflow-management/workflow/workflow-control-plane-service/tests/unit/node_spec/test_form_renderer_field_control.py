"""``ConfigField.field_control`` overrides the rendered FE control name."""

from app.layer1_domain.node_spec.form_renderer import render_input_schema
from app.layer1_domain.node_spec.model import (
    ConfigField,
    FieldType,
    NodeSpec,
)
from app.layer1_domain.node_spec.registry import get_spec
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType


def _spec(field: ConfigField) -> NodeSpec:
    return NodeSpec(
        node_type=NodeType.COMPUTE,
        name="X",
        description="",
        category="test",
        runtime="engine",
        node_class=NodeClass.TECHNICAL,
        icon="",
        color="",
        config_fields=(field,),
    )


def test_field_control_overrides_control_but_keeps_text_shape() -> None:
    spec = _spec(
        ConfigField(
            key="workflow_id", type=FieldType.STRING, label="Workflow ID",
            placeholder="wf-xxxxxxxx", required=True,
            field_control="WorkflowPickerControl",
        )
    )

    [field] = render_input_schema(spec)

    assert field["key"] == "workflow_id"
    assert field["outputType"] == "string"
    config = field["fieldConfig"]
    # Custom control, but the text field shape (wrapper, label, placeholder,
    # required rule) is preserved.
    assert config["fieldControl"] == "WorkflowPickerControl"
    assert config["fieldWrapper"] == "FormItemWrapper"
    assert config["wrapperProps"]["label"] == "Workflow ID"
    assert config["controlProps"]["placeholder"] == "wf-xxxxxxxx"
    assert config["rules"] == [{"method": "required"}]


def test_field_control_without_placeholder_omits_control_props() -> None:
    spec = _spec(
        ConfigField(
            key="workflow_id", type=FieldType.STRING, label="Workflow ID",
            required=True, field_control="WorkflowPickerControl",
        )
    )

    [field] = render_input_schema(spec)
    config = field["fieldConfig"]
    assert config["fieldControl"] == "WorkflowPickerControl"
    assert "controlProps" not in config


def test_workflow_spec_emits_workflow_picker_control() -> None:
    spec = get_spec(NodeType.WORKFLOW)
    [workflow_id] = [
        f for f in render_input_schema(spec) if f["key"] == "workflow_id"
    ]
    assert workflow_id["fieldConfig"]["fieldControl"] == "WorkflowPickerControl"
