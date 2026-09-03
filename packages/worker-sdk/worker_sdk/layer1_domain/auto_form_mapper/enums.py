"""Enumeration types mapping to auto-form TypeScript constants."""

from enum import Enum


class OutputType(str, Enum):
    """Maps to IOutputType."""
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"


class FieldControl(str, Enum):
    """Maps to FieldComponent constants (component registry keys)."""
    INPUT = "InputControl"
    TEXTAREA = "TextareaControl"
    SELECT = "SelectControl"
    SWITCH = "SwitchControl"
    MARKDOWN = "MarkdownControl"
    NODE_POPUP = "NodePopupControl"
    CHECKBOX = "CheckBoxControl"
    RADIO_POPUP = "RadioPopupControl"
    SINGLE_UPLOAD = "UploadControl"
    COMBOBOX = "ComboBoxControl"
    MULTIPLE_COMBOBOX = "MultipleComboboxControl"
    SINGLE_UPLOAD_FIELD_MAPPING = "SingleUploadFieldMappingControl"
    CODE_CONTROL = "CodeControl"


class FieldWrapper(str, Enum):
    """Maps to WrapperComponent constants (component registry keys)."""
    FORM_ITEM = "FormItemWrapper"
    FRAGMENT = "FragmentWrapper"
    TABLE = "TableWrapper"
    TABLE_ROW = "TableRowWrapper"
    TABLE_CELL = "TableCellWrapper"
    DIV = "DivWrapper"
    SEQUENCE = "SequenceWrapper"
    TAB = "TabWrapper"
    TAB_ITEM = "TabItemWrapper"


class ConditionAction(str, Enum):
    """Maps to condition action in IConditionConfig."""
    DISABLED = "disabled"
    INVISIBLE = "invisible"


class ConditionOperator(str, Enum):
    """Maps to EShowWhenOperator enum."""
    EQUALS = "equals"
    NOT_EQUALS = "notEquals"
    EXISTS = "exists"
    NOT_EXISTS = "notExists"
    GREATER_THAN = "greaterThan"
    LESS_THAN = "lessThan"
    GREATER_OR_EQUAL = "greaterOrEqual"
    LESS_OR_EQUAL = "lessOrEqual"
    CONTAINS = "contains"
    IN = "in"


class HttpMethod(str, Enum):
    """HTTP methods for API calls."""
    GET = "get"
    POST = "post"


class StringValidationMethod(str, Enum):
    """Validation methods for string fields."""
    TRIM = "trim"
    INCLUDES = "includes"
    STARTS_WITH = "startsWith"
    ENDS_WITH = "endsWith"
    PATTERN = "pattern"
    REGEX = "regex"


class NumberValidationMethod(str, Enum):
    """Validation methods for number fields."""
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    INT = "int"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MULTIPLE_OF = "multipleOf"


class CommonValidationMethod(str, Enum):
    """Validation methods for both string and number fields."""
    MIN = "min"
    MAX = "max"
    LENGTH = "length"


class CustomValidationMethod(str, Enum):
    """Custom validation methods for advanced validation logic."""
    REFINE = "refine"
    SUPER_REFINE = "superRefine"
