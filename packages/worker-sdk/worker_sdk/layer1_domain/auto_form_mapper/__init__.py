"""Auto-Form Python Object Mapper - Generate auto-form JSON schemas from Python."""

from .enums import (
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
from .models import (
    AutoFormField,
    CallApiConfig,
    ConditionConfig,
    FieldConfig,
    Schema,
    ValidationRule,
)
from .control_props import (
    CheckBoxControlProps,
    ComboBoxControlProps,
    InputControlProps,
    MultipleComboboxControlProps,
    NodePopupControlProps,
    RadioPopupControlProps,
    SelectControlProps,
    SelectOption,
    SingleUploadControlProps,
    SingleUploadFieldMappingControlProps,
    SwitchControlProps,
    TextareaControlProps,
)
from .wrapper_props import (
    DivWrapperProps,
    FormItemWrapperProps,
    SequenceWrapperProps,
    TabItemWrapperProps,
    TableWrapperProps,
)
from .validation import (
    optional_string,
    required,
    required_email,
    required_number,
    required_string,
    url_validation,
)
from .helpers import (
    array_field,
    checkbox_field,
    combobox_field,
    hidden_field,
    markdown_field,
    multiple_combobox_field,
    node_popup_field,
    number_field,
    object_field,
    radio_popup_field,
    select_field,
    single_upload_field,
    single_upload_field_mapping_field,
    switch_field,
    table_array_field,
    text_field,
    textarea_field,
)

__all__ = [
    # Enums
    "OutputType", "FieldControl", "FieldWrapper", "ConditionOperator",
    "ConditionAction", "HttpMethod",
    "StringValidationMethod", "NumberValidationMethod", "CommonValidationMethod",
    "CustomValidationMethod",
    # Core Models
    "Schema", "AutoFormField", "FieldConfig", "ValidationRule",
    "ConditionConfig", "CallApiConfig",
    # Control Props
    "InputControlProps", "TextareaControlProps", "SelectControlProps", "SelectOption",
    "SwitchControlProps", "NodePopupControlProps",
    "CheckBoxControlProps", "ComboBoxControlProps", "MultipleComboboxControlProps",
    "RadioPopupControlProps", "SingleUploadControlProps", "SingleUploadFieldMappingControlProps",
    # Wrapper Props
    "FormItemWrapperProps", "DivWrapperProps", "SequenceWrapperProps",
    "TabItemWrapperProps", "TableWrapperProps",
    # Validation Helpers
    "required", "required_string", "required_email", "required_number",
    "optional_string", "url_validation",
    # Builder Helpers
    "text_field", "number_field", "textarea_field", "select_field",
    "switch_field", "hidden_field", "markdown_field",
    "array_field", "table_array_field", "object_field",
    "checkbox_field", "combobox_field", "multiple_combobox_field",
    "radio_popup_field", "single_upload_field",
    "node_popup_field", "single_upload_field_mapping_field",
]
