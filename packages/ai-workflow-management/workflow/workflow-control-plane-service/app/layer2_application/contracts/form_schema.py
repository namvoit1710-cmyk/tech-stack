"""Strict pydantic v2 DTOs for the auto-form field schema.

Mirrors the dataclasses in ``app.layer1_domain.auto_form_mapper`` but with
``extra="forbid"`` so typos and unknown keys are rejected at the adapter
boundary. These DTOs are the target contract for the v2 palette endpoints;
Phase 0 ships them as importable but not yet wired into any endpoint.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Enums (parallel to auto_form_mapper.enums; duplicated so layer3 keeps a
# stable adapter-side contract independent of domain refactors).
# ---------------------------------------------------------------------------


class OutputType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"


class FieldControl(str, Enum):
    INPUT = "InputControl"
    TEXTAREA = "TextareaControl"
    SELECT = "SelectControl"
    SWITCH = "SwitchControl"
    MARKDOWN = "MarkdownControl"
    NODE_POPUP = "NodePopupControl"
    CHECKBOX = "CheckBoxControl"
    RADIO_POPUP = "RadioPopupControl"
    SINGLE_UPLOAD = "UploadControl"
    # FE registry key is "ComboboxControl" (lower-case b) — the FE never emits
    # a separate MultipleComboboxControl (Combobox handles single/multiple
    # internally under this one key). SA-2184: reconciled with the FE so a
    # Combobox field no longer fails this enum before `_validate_shape` runs.
    COMBOBOX = "ComboboxControl"
    MULTIPLE_COMBOBOX = "MultipleComboboxControl"
    # SA-2192: the richest option-source control (server pagination + total +
    # typeahead + contextValues). Was previously absent from this enum, so
    # WorkerNodeProvider's lenient FormField.model_validate silently dropped
    # every SelectTable field before `_validate_shape` ever ran.
    SELECT_TABLE = "SelectTableControl"
    SINGLE_UPLOAD_FIELD_MAPPING = "SingleUploadFieldMappingControl"
    CODE_CONTROL = "CodeControl"
    # Custom FE control (searchable workflow picker) for WORKFLOW.workflow_id;
    # behaves like a text/string field on the contract side.
    WORKFLOW_PICKER = "WorkflowPickerControl"
    # Custom FE control (searchable environment picker) for WORKFLOW.environment_id
    # (SA-2458); behaves like a text/string field on the contract side.
    ENVIRONMENT_PICKER = "EnvironmentPickerControl"
    # Custom FE control (visual form builder) for the INPUT node's input_schema;
    # emits a JSON-schema string, so it behaves like a text/string field on the
    # contract side (SA-2085).
    FORM_BUILDER = "FormBuilderControl"


class FieldWrapper(str, Enum):
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
    DISABLED = "disabled"
    INVISIBLE = "invisible"


class ConditionOperator(str, Enum):
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


class ValidationMethod(str, Enum):
    """Union of validation methods. FE checks against its own registry."""
    # presence
    REQUIRED = "required"
    # common
    MIN = "min"
    MAX = "max"
    LENGTH = "length"
    # string
    TRIM = "trim"
    INCLUDES = "includes"
    STARTS_WITH = "startsWith"
    ENDS_WITH = "endsWith"
    PATTERN = "pattern"
    REGEX = "regex"
    # number
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    INT = "int"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MULTIPLE_OF = "multipleOf"
    # custom
    REFINE = "refine"
    SUPER_REFINE = "superRefine"


# ---------------------------------------------------------------------------
# Leaf models
# ---------------------------------------------------------------------------


class SelectOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: Any


class ValidationRule(BaseModel):
    """Single validation rule — matches ``{method, value?, message?}``."""
    model_config = ConfigDict(extra="forbid")

    method: ValidationMethod
    value: Optional[Any] = None
    message: Optional[str] = None


class ConditionConfig(BaseModel):
    """Show/hide or disable this field based on another field's value."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    field_key: str = Field(alias="fieldKey")
    condition_value: Any = Field(alias="conditionValue")
    action: ConditionAction = ConditionAction.INVISIBLE
    operator: ConditionOperator = ConditionOperator.EQUALS


class ControlProps(BaseModel):
    """Props passed to the field's control component.

    Deliberately permissive at the leaf — each FieldControl has its own
    allowable shape; FormField.validate enforces control-specific constraints.
    """
    model_config = ConfigDict(extra="allow")


class WrapperProps(BaseModel):
    """Props passed to the field's wrapper component."""
    model_config = ConfigDict(extra="allow")


class FieldConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    field_wrapper: Optional[FieldWrapper] = Field(default=None, alias="fieldWrapper")
    wrapper_props: Optional[WrapperProps] = Field(default=None, alias="wrapperProps")
    field_control: Optional[FieldControl] = Field(default=None, alias="fieldControl")
    control_props: Optional[ControlProps] = Field(default=None, alias="controlProps")
    rules: Optional[list[ValidationRule]] = None
    condition: Optional[ConditionConfig] = None
    initial_api_config: Optional[dict[str, Any]] = Field(
        default=None, alias="initialApiConfig"
    )


# ---------------------------------------------------------------------------
# Form field (nestable)
# ---------------------------------------------------------------------------


class FormField(BaseModel):
    """A single node input-schema field.

    The frontend's auto-form library renders one of these per visible input.
    Nested fields (``array``/``object``) get ``fields`` populated.
    """
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    key: str = Field(min_length=1)
    output_type: OutputType = Field(alias="outputType")
    field_config: FieldConfig = Field(alias="fieldConfig")
    fields: Optional[list["FormField"]] = None

    @model_validator(mode="after")
    def _validate_shape(self) -> "FormField":
        fc = self.field_config
        control = fc.field_control

        # ARRAY / OBJECT outputs must declare nested fields — UNLESS the
        # field is display-only (readonly output value) or edited as raw
        # text via CodeControl (JSON editor). In those cases no nested form
        # is rendered, so `fields` is allowed to be empty or omitted.
        if self.output_type in (OutputType.ARRAY, OutputType.OBJECT):
            is_readonly = bool(
                fc.control_props
                and fc.control_props.model_dump().get("readonly")
            )
            is_code_control = control == FieldControl.CODE_CONTROL
            nested_form_needed = not (is_readonly or is_code_control)
            if nested_form_needed and self.fields is None:
                raise ValueError(
                    f"FormField(key={self.key!r}): output_type={self.output_type.value!r} "
                    "requires `fields` (nested schema) unless readonly or CodeControl"
                )

        # SELECT-like controls must declare options (or a dynamic data source)
        # in control_props.
        if control in (FieldControl.SELECT, FieldControl.RADIO_POPUP,
                       FieldControl.COMBOBOX, FieldControl.MULTIPLE_COMBOBOX,
                       FieldControl.SELECT_TABLE):
            cp_dict = fc.control_props.model_dump() if fc.control_props is not None else {}
            options = cp_dict.get("options")
            # `serverOptions` is the canonical dynamic option-source key the FE
            # actually emits (gateway or legacy REST); `dynamic_source` /
            # `dynamicSource` are recognised too but are phantoms the FE never
            # sends. SA-2184: without `serverOptions` here, WorkerNodeProvider's
            # lenient FormField.model_validate silently dropped every
            # server-sourced Select/Radio/Combobox field.
            dynamic = (
                cp_dict.get("dynamic_source")
                or cp_dict.get("dynamicSource")
                or cp_dict.get("serverOptions")
            )
            if options is None and dynamic is None:
                raise ValueError(
                    f"FormField(key={self.key!r}): control={control.value!r} "
                    "requires `options` or `dynamic_source`/`serverOptions` in controlProps"
                )

        # SWITCH / CHECKBOX must bind to a boolean output.
        if control in (FieldControl.SWITCH, FieldControl.CHECKBOX):
            if self.output_type != OutputType.BOOLEAN:
                raise ValueError(
                    f"FormField(key={self.key!r}): control={control.value!r} "
                    f"requires output_type='boolean', got {self.output_type.value!r}"
                )

        return self


FormField.model_rebuild()


class FormSchema(BaseModel):
    """Root-level schema wrapper."""
    model_config = ConfigDict(extra="forbid")

    fields: list[FormField] = Field(default_factory=list)
