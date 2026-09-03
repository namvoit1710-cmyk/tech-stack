import json

from worker_sdk.layer1_domain.auto_form_mapper.enums import (
    ConditionAction,
    ConditionOperator,
    HttpMethod,
    OutputType,
)
from worker_sdk.layer1_domain.auto_form_mapper.models import (
    AutoFormField,
    CallApiConfig,
    ConditionConfig,
    FieldConfig,
    Schema,
    ValidationRule,
)


# --- CallApiConfig ---

def test_call_api_config_to_dict_all_fields():
    cfg = CallApiConfig(
        endpoint="/api/options",
        method=HttpMethod.POST,
        body={"key": "val"},
        query_params={"q": "search"},
    )
    d = cfg.to_dict()
    assert d == {
        "endpoint": "/api/options",
        "method": "post",
        "body": {"key": "val"},
        "queryParams": {"q": "search"},
    }


def test_call_api_config_to_dict_empty():
    cfg = CallApiConfig()
    assert cfg.to_dict() == {}


def test_call_api_config_to_dict_partial():
    cfg = CallApiConfig(endpoint="/api/test")
    assert cfg.to_dict() == {"endpoint": "/api/test"}


# --- ConditionConfig ---

def test_condition_config_to_dict_defaults():
    cfg = ConditionConfig(field_key="type", condition_value="admin")
    d = cfg.to_dict()
    assert d == {
        "fieldKey": "type",
        "conditionValue": "admin",
        "action": "invisible",
        "operator": "equals",
    }


def test_condition_config_to_dict_custom():
    cfg = ConditionConfig(
        field_key="type",
        condition_value="admin",
        action=ConditionAction.DISABLED,
        operator=ConditionOperator.NOT_EQUALS,
    )
    d = cfg.to_dict()
    assert d["action"] == "disabled"
    assert d["operator"] == "notEquals"


def test_condition_config_list_condition():
    cfg = ConditionConfig(field_key="role", condition_value=["admin", "editor"])
    d = cfg.to_dict()
    assert d["conditionValue"] == ["admin", "editor"]


# --- ValidationRule ---

def test_validation_rule_to_dict_with_value():
    vr = ValidationRule(method="min", value=3, message="Too short")
    assert vr.to_dict() == {"method": "min", "value": 3, "message": "Too short"}


def test_validation_rule_to_dict_method_only():
    vr = ValidationRule(method="required")
    d = vr.to_dict()
    assert d == {"method": "required"}
    assert "value" not in d
    assert "message" not in d


def test_validation_rule_to_dict_with_message_no_value():
    vr = ValidationRule(method="int", message="Must be a whole number")
    d = vr.to_dict()
    assert d == {"method": "int", "message": "Must be a whole number"}
    assert "value" not in d


# --- FieldConfig ---

def test_field_config_to_dict_all_fields():
    condition = ConditionConfig(field_key="type", condition_value="admin")
    rules = [ValidationRule(method="required")]
    cfg = FieldConfig(
        field_wrapper="FormItemWrapper",
        wrapper_props={"label": "Name", "required": True},
        field_control="InputControl",
        control_props={"placeholder": "Enter name"},
        rules=rules,
        condition=condition,
        initial_api_config={"endpoint": "/api"},
    )
    d = cfg.to_dict()
    assert d == {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": {"label": "Name", "required": True},
        "fieldControl": "InputControl",
        "controlProps": {"placeholder": "Enter name"},
        "rules": [{"method": "required"}],
        "condition": {
            "fieldKey": "type",
            "conditionValue": "admin",
            "action": "invisible",
            "operator": "equals",
        },
        "initialApiConfig": {"endpoint": "/api"},
    }


def test_field_config_to_dict_none_fields_omitted():
    cfg = FieldConfig(field_wrapper="FormItemWrapper")
    d = cfg.to_dict()
    assert d == {"fieldWrapper": "FormItemWrapper"}
    assert "rules" not in d
    assert "condition" not in d
    assert "initialApiConfig" not in d


def test_field_config_to_dict_empty_rules_omitted():
    cfg = FieldConfig(rules=[])
    assert "rules" not in cfg.to_dict()


# --- AutoFormField ---

def test_auto_form_field_to_dict_minimal():
    f = AutoFormField(
        key="name",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control="InputControl",
            wrapper_props={"label": "Name"},
        ),
    )
    d = f.to_dict()
    assert d["key"] == "name"
    assert d["outputType"] == "string"
    assert d["fieldConfig"]["wrapperProps"]["label"] == "Name"
    assert "fields" not in d


def test_auto_form_field_to_dict_nested():
    child = AutoFormField(
        key="street",
        output_type=OutputType.STRING,
        field_config=FieldConfig(wrapper_props={"label": "Street"}),
    )
    parent = AutoFormField(
        key="address",
        output_type=OutputType.OBJECT,
        field_config=FieldConfig(wrapper_props={"label": "Address"}),
        fields=[child],
    )
    d = parent.to_dict()
    assert len(d["fields"]) == 1
    assert d["fields"][0]["key"] == "street"


def test_auto_form_field_no_default_or_rules_at_top_level():
    """AutoFormField no longer has default or rules at the top level."""
    f = AutoFormField(
        key="test",
        output_type=OutputType.STRING,
        field_config=FieldConfig(),
    )
    d = f.to_dict()
    assert "default" not in d
    assert "rules" not in d


# --- Schema ---

def test_schema_to_dict():
    schema = Schema(fields=[
        AutoFormField(
            key="name",
            output_type=OutputType.STRING,
            field_config=FieldConfig(wrapper_props={"label": "Name"}),
        ),
    ])
    d = schema.to_dict()
    assert "fields" in d
    assert len(d["fields"]) == 1
    assert d["fields"][0]["key"] == "name"


def test_schema_to_json():
    schema = Schema(fields=[
        AutoFormField(
            key="test",
            output_type=OutputType.STRING,
            field_config=FieldConfig(wrapper_props={"label": "Test"}),
        ),
    ])
    json_str = schema.to_json()
    parsed = json.loads(json_str)
    assert parsed["fields"][0]["key"] == "test"
    assert parsed["fields"][0]["outputType"] == "string"


def test_schema_to_json_indent():
    schema = Schema(fields=[
        AutoFormField(
            key="x",
            output_type=OutputType.NUMBER,
            field_config=FieldConfig(),
        ),
    ])
    json_str = schema.to_json(indent=4)
    assert "    " in json_str


def test_schema_empty_fields():
    schema = Schema(fields=[])
    assert schema.to_dict() == {"fields": []}
