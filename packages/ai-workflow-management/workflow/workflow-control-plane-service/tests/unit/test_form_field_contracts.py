"""Tests for the pydantic FormField contract."""

import pytest
from pydantic import ValidationError

from app.layer1_domain.node_spec import BUILTIN_SPECS
from app.layer1_domain.node_spec.form_renderer import (
    render_input_schema,
    render_output_schema,
)

SCHEMAS = {
    spec.node_type.value: {
        "input_schema": render_input_schema(spec),
        "output_schema": render_output_schema(spec),
    }
    for spec in BUILTIN_SPECS.values()
    if spec.node_type.value not in ("TASK", "LOOP_EXIT", "LOOP_CONTINUE")
}
from app.layer2_application.contracts import (
    FieldConfig,
    FieldControl,
    FieldWrapper,
    FormField,
    OutputType,
)


class TestFormFieldHappyPath:
    def test_simple_input_field(self):
        field = FormField.model_validate({
            "key": "url",
            "outputType": "string",
            "fieldConfig": {
                "fieldControl": "InputControl",
                "fieldWrapper": "FormItemWrapper",
            },
        })
        assert field.key == "url"
        assert field.output_type == OutputType.STRING
        assert field.field_config.field_control == FieldControl.INPUT
        assert field.field_config.field_wrapper == FieldWrapper.FORM_ITEM

    def test_snake_case_aliases_also_accepted(self):
        # populate_by_name=True means snake_case works too.
        field = FormField.model_validate({
            "key": "url",
            "output_type": "string",
            "field_config": {
                "field_control": "InputControl",
            },
        })
        assert field.output_type == OutputType.STRING

    def test_switch_with_boolean_output(self):
        field = FormField.model_validate({
            "key": "enabled",
            "outputType": "boolean",
            "fieldConfig": {"fieldControl": "SwitchControl"},
        })
        assert field.output_type == OutputType.BOOLEAN

    def test_select_with_options(self):
        field = FormField.model_validate({
            "key": "color",
            "outputType": "string",
            "fieldConfig": {
                "fieldControl": "SelectControl",
                "controlProps": {
                    "options": [
                        {"label": "Red", "value": "red"},
                        {"label": "Blue", "value": "blue"},
                    ],
                },
            },
        })
        assert field.field_config.field_control == FieldControl.SELECT

    def test_select_with_dynamic_source_instead_of_options(self):
        FormField.model_validate({
            "key": "table",
            "outputType": "string",
            "fieldConfig": {
                "fieldControl": "SelectControl",
                "controlProps": {
                    "dynamic_source": {"worker": "db", "function": "list_tables"},
                },
            },
        })

    def test_select_with_server_options_instead_of_options(self):
        # SA-2184: `serverOptions` is the key the FE actually emits (gateway or
        # legacy REST) — must not be dropped as a "missing options" rejection.
        field = FormField.model_validate({
            "key": "country",
            "outputType": "string",
            "fieldConfig": {
                "fieldControl": "SelectControl",
                "controlProps": {
                    "serverOptions": {"service": "workflow", "endpoint": "odata/Country/data"},
                },
            },
        })
        assert field.field_config.field_control == FieldControl.SELECT

    def test_combobox_with_server_options_uses_fe_registry_key(self):
        # SA-2184: FE registry key is "ComboboxControl" (lower-case b); the old
        # "ComboBoxControl" enum value rejected every Combobox field before
        # `_validate_shape` even ran.
        field = FormField.model_validate({
            "key": "country",
            "outputType": "string",
            "fieldConfig": {
                "fieldControl": "ComboboxControl",
                "controlProps": {
                    "serverOptions": {"service": "workflow", "endpoint": "odata/Country/data"},
                },
            },
        })
        assert field.field_config.field_control == FieldControl.COMBOBOX
        assert FieldControl.COMBOBOX.value == "ComboboxControl"

    def test_combobox_without_options_or_source_rejected(self):
        with pytest.raises(ValidationError, match="options"):
            FormField.model_validate({
                "key": "country",
                "outputType": "string",
                "fieldConfig": {"fieldControl": "ComboboxControl"},
            })

    def test_select_table_with_server_options_instead_of_options(self):
        # SA-2192: SelectTableControl was previously absent from FieldControl,
        # so any SelectTable field was dropped before `_validate_shape` ran.
        field = FormField.model_validate({
            "key": "picked_row",
            "outputType": "object",
            "fieldConfig": {
                "fieldControl": "SelectTableControl",
                "controlProps": {
                    "serverOptions": {
                        "service": "workflow",
                        "endpoint": "workers/gateway/functions/invoke_app_function/invoke",
                        "method": "POST",
                        "responseMapping": {
                            "dataPath": "data.result.body.data",
                            "totalPath": "data.result.body.total_count",
                        },
                    },
                },
            },
            "fields": [
                {
                    "key": "id",
                    "outputType": "string",
                    "fieldConfig": {"fieldControl": "InputControl"},
                }
            ],
        })
        assert field.field_config.field_control == FieldControl.SELECT_TABLE
        assert FieldControl.SELECT_TABLE.value == "SelectTableControl"

    def test_select_table_without_options_or_source_rejected(self):
        with pytest.raises(ValidationError, match="options"):
            FormField.model_validate({
                "key": "picked_row",
                "outputType": "object",
                "fieldConfig": {"fieldControl": "SelectTableControl"},
                "fields": [
                    {
                        "key": "id",
                        "outputType": "string",
                        "fieldConfig": {"fieldControl": "InputControl"},
                    }
                ],
            })


class TestFormFieldRejections:
    def test_unknown_root_key_rejected(self):
        with pytest.raises(ValidationError):
            FormField.model_validate({
                "key": "url",
                "outputType": "string",
                "fieldConfig": {"fieldControl": "InputControl"},
                "extra_key": "nope",
            })

    def test_empty_key_rejected(self):
        with pytest.raises(ValidationError):
            FormField.model_validate({
                "key": "",
                "outputType": "string",
                "fieldConfig": {"fieldControl": "InputControl"},
            })

    def test_invalid_output_type_rejected(self):
        with pytest.raises(ValidationError):
            FormField.model_validate({
                "key": "x",
                "outputType": "weird",
                "fieldConfig": {"fieldControl": "InputControl"},
            })

    def test_invalid_field_control_rejected(self):
        with pytest.raises(ValidationError):
            FormField.model_validate({
                "key": "x",
                "outputType": "string",
                "fieldConfig": {"fieldControl": "NotAControl"},
            })

    def test_select_without_options_rejected(self):
        with pytest.raises(ValidationError, match="options"):
            FormField.model_validate({
                "key": "color",
                "outputType": "string",
                "fieldConfig": {"fieldControl": "SelectControl"},
            })

    def test_switch_with_non_boolean_output_rejected(self):
        with pytest.raises(ValidationError, match="boolean"):
            FormField.model_validate({
                "key": "enabled",
                "outputType": "string",
                "fieldConfig": {"fieldControl": "SwitchControl"},
            })

    def test_array_without_nested_fields_rejected(self):
        with pytest.raises(ValidationError, match="requires `fields`"):
            FormField.model_validate({
                "key": "items",
                "outputType": "array",
                "fieldConfig": {"fieldControl": "InputControl"},
            })

    def test_object_without_nested_fields_rejected(self):
        with pytest.raises(ValidationError, match="requires `fields`"):
            FormField.model_validate({
                "key": "settings",
                "outputType": "object",
                "fieldConfig": {"fieldControl": "InputControl"},
            })


class TestFormFieldNested:
    def test_array_with_nested_fields(self):
        field = FormField.model_validate({
            "key": "items",
            "outputType": "array",
            "fieldConfig": {"fieldControl": "InputControl"},
            "fields": [
                {
                    "key": "name",
                    "outputType": "string",
                    "fieldConfig": {"fieldControl": "InputControl"},
                }
            ],
        })
        assert field.fields is not None
        assert len(field.fields) == 1
        assert field.fields[0].key == "name"


class TestValidationRule:
    def test_rule_accepts_known_method(self):
        field = FormField.model_validate({
            "key": "email",
            "outputType": "string",
            "fieldConfig": {
                "fieldControl": "InputControl",
                "rules": [
                    {"method": "min", "value": 3, "message": "too short"},
                    {"method": "regex", "value": "^.+@.+$"},
                ],
            },
        })
        assert len(field.field_config.rules) == 2

    def test_rule_rejects_unknown_method(self):
        with pytest.raises(ValidationError):
            FormField.model_validate({
                "key": "email",
                "outputType": "string",
                "fieldConfig": {
                    "fieldControl": "InputControl",
                    "rules": [{"method": "nonexistent_method"}],
                },
            })


class TestBuiltinSchemaRoundTrip:
    """Every builtin input_schema and output_schema field must round-trip
    through FormField.model_validate. This is the tripwire that catches
    builtin schemas drifting from the strict contract — when the palette
    starts returning FormField-typed DTOs in Phase 1, anything that fails
    here would break the /v2/node-catalog endpoint."""

    @pytest.mark.parametrize("node_type", list(SCHEMAS.keys()))
    def test_input_schema_roundtrip(self, node_type):
        for field_dict in SCHEMAS[node_type]["input_schema"]:
            FormField.model_validate(field_dict)

    @pytest.mark.parametrize("node_type", list(SCHEMAS.keys()))
    def test_output_schema_roundtrip(self, node_type):
        for field_dict in SCHEMAS[node_type]["output_schema"]:
            FormField.model_validate(field_dict)
