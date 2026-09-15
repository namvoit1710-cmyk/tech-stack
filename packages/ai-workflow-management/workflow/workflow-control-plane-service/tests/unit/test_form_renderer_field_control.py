from app.layer1_domain.node_spec.form_renderer import _render_control
from app.layer1_domain.node_spec.model import ConfigField, FieldType


def test_field_control_override_merges_render_props_into_wrapper_props():
    f = ConfigField(
        key="input_schema",
        type=FieldType.OBJECT,
        label="Input Schema",
        field_control="FormBuilderControl",
        render_props={"labelSpan": "xl-12", "fieldSpan": "xl-12"},
    )
    d = _render_control(f).to_dict()
    assert d["outputType"] == "string"
    assert d["fieldConfig"]["fieldControl"] == "FormBuilderControl"
    assert d["fieldConfig"]["wrapperProps"]["label"] == "Input Schema"
    assert d["fieldConfig"]["wrapperProps"]["labelSpan"] == "xl-12"
    assert d["fieldConfig"]["wrapperProps"]["fieldSpan"] == "xl-12"
