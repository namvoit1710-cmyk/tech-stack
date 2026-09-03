from worker_sdk.layer1_domain.auto_form_mapper.enums import (
    CommonValidationMethod,
    ConditionAction,
    ConditionOperator,
    CustomValidationMethod,
    FieldControl,
    FieldWrapper,
    HttpMethod,
    NumberValidationMethod,
    OutputType,
    StringValidationMethod,
)


# --- OutputType ---

def test_output_type_values():
    assert OutputType.STRING == "string"
    assert OutputType.NUMBER == "number"
    assert OutputType.BOOLEAN == "boolean"
    assert OutputType.OBJECT == "object"
    assert OutputType.ARRAY == "array"


def test_output_type_is_str():
    assert isinstance(OutputType.STRING, str)


# --- FieldControl ---

def test_field_control_values():
    assert FieldControl.INPUT == "InputControl"
    assert FieldControl.TEXTAREA == "TextareaControl"
    assert FieldControl.SELECT == "SelectControl"
    assert FieldControl.SWITCH == "SwitchControl"
    assert FieldControl.MARKDOWN == "MarkdownControl"
    assert FieldControl.NODE_POPUP == "NodePopupControl"
    assert FieldControl.CHECKBOX == "CheckBoxControl"
    assert FieldControl.RADIO_POPUP == "RadioPopupControl"
    assert FieldControl.SINGLE_UPLOAD == "UploadControl"
    assert FieldControl.COMBOBOX == "ComboBoxControl"
    assert FieldControl.MULTIPLE_COMBOBOX == "MultipleComboboxControl"
    assert FieldControl.SINGLE_UPLOAD_FIELD_MAPPING == "SingleUploadFieldMappingControl"


def test_field_control_removed_members():
    """SEARCH and NONE should no longer exist."""
    assert not hasattr(FieldControl, "SEARCH")
    assert not hasattr(FieldControl, "NONE")


# --- FieldWrapper ---

def test_field_wrapper_values():
    assert FieldWrapper.FORM_ITEM == "FormItemWrapper"
    assert FieldWrapper.FRAGMENT == "FragmentWrapper"
    assert FieldWrapper.TABLE == "TableWrapper"
    assert FieldWrapper.TABLE_ROW == "TableRowWrapper"
    assert FieldWrapper.TABLE_CELL == "TableCellWrapper"
    assert FieldWrapper.DIV == "DivWrapper"
    assert FieldWrapper.SEQUENCE == "SequenceWrapper"
    assert FieldWrapper.TAB == "TabWrapper"
    assert FieldWrapper.TAB_ITEM == "TabItemWrapper"


def test_field_wrapper_removed_members():
    """Old wrappers should no longer exist."""
    for name in ("FLEX_WRAPPER", "GRID_WRAPPER", "FORM_WRAPPER",
                 "ARRAY_FORM_WRAPPER", "DIALOG_WRAPPER",
                 "FRAGMENT_WRAPPER", "TABLE_WRAPPER",
                 "TABLE_ROW_WRAPPER", "TABLE_CELL_WRAPPER"):
        assert not hasattr(FieldWrapper, name)


# --- ConditionAction ---

def test_condition_action_values():
    assert ConditionAction.DISABLED == "disabled"
    assert ConditionAction.INVISIBLE == "invisible"


# --- ConditionOperator ---

def test_condition_operator_values():
    assert ConditionOperator.EQUALS == "equals"
    assert ConditionOperator.NOT_EQUALS == "notEquals"
    assert ConditionOperator.EXISTS == "exists"
    assert ConditionOperator.NOT_EXISTS == "notExists"
    assert ConditionOperator.GREATER_THAN == "greaterThan"
    assert ConditionOperator.LESS_THAN == "lessThan"
    assert ConditionOperator.GREATER_OR_EQUAL == "greaterOrEqual"
    assert ConditionOperator.LESS_OR_EQUAL == "lessOrEqual"
    assert ConditionOperator.CONTAINS == "contains"
    assert ConditionOperator.IN == "in"


# --- HttpMethod ---

def test_http_method_values():
    assert HttpMethod.GET == "get"
    assert HttpMethod.POST == "post"


# --- StringValidationMethod ---

def test_string_validation_method_values():
    assert StringValidationMethod.TRIM == "trim"
    assert StringValidationMethod.INCLUDES == "includes"
    assert StringValidationMethod.STARTS_WITH == "startsWith"
    assert StringValidationMethod.ENDS_WITH == "endsWith"
    assert StringValidationMethod.PATTERN == "pattern"
    assert StringValidationMethod.REGEX == "regex"


# --- NumberValidationMethod ---

def test_number_validation_method_values():
    assert NumberValidationMethod.GT == "gt"
    assert NumberValidationMethod.GTE == "gte"
    assert NumberValidationMethod.LT == "lt"
    assert NumberValidationMethod.LTE == "lte"
    assert NumberValidationMethod.INT == "int"
    assert NumberValidationMethod.POSITIVE == "positive"
    assert NumberValidationMethod.NEGATIVE == "negative"
    assert NumberValidationMethod.MULTIPLE_OF == "multipleOf"


# --- CommonValidationMethod ---

def test_common_validation_method_values():
    assert CommonValidationMethod.MIN == "min"
    assert CommonValidationMethod.MAX == "max"
    assert CommonValidationMethod.LENGTH == "length"


# --- String serialization ---

def test_enum_str_serialization():
    """All enums should be usable as plain strings."""
    assert OutputType.STRING.value == "string"
    assert f"{FieldControl.INPUT.value}" == "InputControl"


# --- CustomValidationMethod ---

def test_custom_validation_method_values():
    assert CustomValidationMethod.REFINE == "refine"
    assert CustomValidationMethod.SUPER_REFINE == "superRefine"
