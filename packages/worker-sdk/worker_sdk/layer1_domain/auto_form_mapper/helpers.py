"""Convenience builder functions for common field patterns."""

from __future__ import annotations

from typing import Any, Optional

from .enums import FieldControl, FieldWrapper, OutputType
from .models import AutoFormField, FieldConfig, ValidationRule


def _has_required(rules: Optional[list[ValidationRule]]) -> bool:
    """Check if rules contain a 'required' method."""
    if not rules:
        return False
    return any(r.method == "required" for r in rules)


def _wrapper_props_with_label(
    label: str,
    rules: Optional[list[ValidationRule]] = None,
) -> dict[str, Any]:
    """Build wrapperProps dict with label and optional required flag."""
    props: dict[str, Any] = {"label": label}
    if _has_required(rules):
        props["required"] = True
    return props


def _add_if_set(props: dict[str, Any], key: str, value: Any) -> None:
    """Add key-value to props dict if value is not None."""
    if value is not None:
        props[key] = value


def text_field(
    key: str,
    label: str,
    placeholder: Optional[str] = None,
    rules: Optional[list[ValidationRule]] = None,
    default: Optional[str] = None,
    readonly: Optional[bool] = None,
    disabled: Optional[bool] = None,
) -> AutoFormField:
    """Create a simple text input field."""
    control_props: dict[str, Any] = {}
    _add_if_set(control_props, "placeholder", placeholder)
    _add_if_set(control_props, "value", default)
    _add_if_set(control_props, "readonly", readonly)
    _add_if_set(control_props, "disabled", disabled)
    return AutoFormField(
        key=key,
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=_wrapper_props_with_label(label, rules),
            control_props=control_props if control_props else None,
            rules=rules,
        ),
    )


def number_field(
    key: str,
    label: str,
    placeholder: Optional[str] = None,
    rules: Optional[list[ValidationRule]] = None,
    default: Optional[float] = None,
    readonly: Optional[bool] = None,
    disabled: Optional[bool] = None,
) -> AutoFormField:
    """Create a number input field."""
    control_props: dict[str, Any] = {"type": "number"}
    _add_if_set(control_props, "placeholder", placeholder)
    _add_if_set(control_props, "value", default)
    _add_if_set(control_props, "readonly", readonly)
    _add_if_set(control_props, "disabled", disabled)
    return AutoFormField(
        key=key,
        output_type=OutputType.NUMBER,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=_wrapper_props_with_label(label, rules),
            control_props=control_props,
            rules=rules,
        ),
    )


def textarea_field(
    key: str,
    label: str,
    placeholder: Optional[str] = None,
    rows: int = 3,
    growing: bool = False,
    rules: Optional[list[ValidationRule]] = None,
    readonly: Optional[bool] = None,
    disabled: Optional[bool] = None,
) -> AutoFormField:
    """Create a textarea field."""
    control_props: dict[str, Any] = {"rowsLength": rows}
    _add_if_set(control_props, "placeholder", placeholder)
    if growing:
        control_props["growing"] = True
    _add_if_set(control_props, "readonly", readonly)
    _add_if_set(control_props, "disabled", disabled)
    return AutoFormField(
        key=key,
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=_wrapper_props_with_label(label, rules),
            control_props=control_props,
            rules=rules,
        ),
    )


def select_field(
    key: str,
    label: str,
    options: list[tuple[str, str]],
    placeholder: Optional[str] = None,
    rules: Optional[list[ValidationRule]] = None,
) -> AutoFormField:
    """Create a static select field.

    Args:
        options: List of (id, display_value) tuples.
    """
    return AutoFormField(
        key=key,
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.SELECT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=_wrapper_props_with_label(label, rules),
            control_props={
                "placeholder": placeholder or f"Select {label.lower()}",
                "options": [{"id": oid, "value": oval} for oid, oval in options],
            },
            rules=rules,
        ),
    )


def switch_field(
    key: str,
    label: str,
    default: bool = False,
    readonly: Optional[bool] = None,
    disabled: Optional[bool] = None,
) -> AutoFormField:
    """Create a boolean switch field."""
    control_props: dict[str, Any] = {}
    if default is not False:
        control_props["value"] = default
    _add_if_set(control_props, "readonly", readonly)
    _add_if_set(control_props, "disabled", disabled)
    return AutoFormField(
        key=key,
        output_type=OutputType.BOOLEAN,
        field_config=FieldConfig(
            field_control=FieldControl.SWITCH,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=_wrapper_props_with_label(label),
            control_props=control_props if control_props else None,
        ),
    )


def hidden_field(key: str, default: Any = None) -> AutoFormField:
    """Create a hidden field (stores data, no UI)."""
    control_props: dict[str, Any] = {}
    if default is not None:
        control_props["value"] = default
    return AutoFormField(
        key=key,
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            control_props=control_props if control_props else None,
        ),
    )


def markdown_field(key: str, content: str, label: Optional[str] = None) -> AutoFormField:
    """Create a read-only markdown display field."""
    wrapper_props: Optional[dict[str, Any]] = None
    if label is not None:
        wrapper_props = {"label": label}
    return AutoFormField(
        key=key,
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.MARKDOWN,
            wrapper_props=wrapper_props,
            control_props={"value": content},
        ),
    )


def array_field(
    key: str,
    label: str,
    item_fields: list[AutoFormField],
    wrapper: FieldWrapper = FieldWrapper.SEQUENCE,
    item_wrapper: FieldWrapper = FieldWrapper.FRAGMENT,
    item_wrapper_props: Optional[dict] = None,
) -> AutoFormField:
    """Create an array field with proper template structure."""
    return AutoFormField(
        key=key,
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_wrapper=wrapper,
            wrapper_props={"label": label},
        ),
        fields=[
            AutoFormField(
                key="0",
                output_type=OutputType.OBJECT,
                field_config=FieldConfig(
                    field_wrapper=item_wrapper,
                    wrapper_props=item_wrapper_props,
                ),
                fields=item_fields,
            ),
        ],
    )


def table_array_field(
    key: str,
    label: str,
    columns: list[AutoFormField],
) -> AutoFormField:
    """Create a table array field. Columns auto-wrapped in TableCellWrapper."""
    table_columns = []
    for col in columns:
        cfg = col.field_config
        table_columns.append(AutoFormField(
            key=col.key,
            output_type=col.output_type,
            field_config=FieldConfig(
                field_wrapper=FieldWrapper.TABLE_CELL,
                wrapper_props=cfg.wrapper_props,
                field_control=cfg.field_control,
                control_props=cfg.control_props,
                rules=cfg.rules,
            ),
            fields=col.fields,
        ))
    return AutoFormField(
        key=key,
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_wrapper=FieldWrapper.TABLE,
            wrapper_props={"label": label},
        ),
        fields=[
            AutoFormField(
                key="0",
                output_type=OutputType.OBJECT,
                field_config=FieldConfig(
                    field_wrapper=FieldWrapper.TABLE_ROW,
                    wrapper_props={"isAdd": True, "isDelete": True},
                ),
                fields=table_columns,
            ),
        ],
    )


def object_field(
    key: str,
    label: Optional[str],
    children: list[AutoFormField],
    wrapper: FieldWrapper = FieldWrapper.FRAGMENT,
    wrapper_props: Optional[dict] = None,
) -> AutoFormField:
    """Create an object container field."""
    wp = wrapper_props or {}
    if label is not None:
        wp["label"] = label
    return AutoFormField(
        key=key,
        output_type=OutputType.OBJECT,
        field_config=FieldConfig(
            field_wrapper=wrapper,
            wrapper_props=wp if wp else None,
        ),
        fields=children,
    )


def checkbox_field(
    key: str,
    label: str,
    rules: Optional[list[ValidationRule]] = None,
    readonly: Optional[bool] = None,
    disabled: Optional[bool] = None,
) -> AutoFormField:
    """Create a checkbox field."""
    control_props: dict[str, Any] = {}
    _add_if_set(control_props, "label", label)
    _add_if_set(control_props, "readonly", readonly)
    _add_if_set(control_props, "disabled", disabled)
    return AutoFormField(
        key=key,
        output_type=OutputType.BOOLEAN,
        field_config=FieldConfig(
            field_control=FieldControl.CHECKBOX,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=_wrapper_props_with_label(label, rules),
            control_props=control_props if control_props else None,
            rules=rules,
        ),
    )


def combobox_field(
    key: str,
    label: str,
    options: list[tuple[str, str]],
    placeholder: Optional[str] = None,
    rules: Optional[list[ValidationRule]] = None,
    readonly: Optional[bool] = None,
    disabled: Optional[bool] = None,
) -> AutoFormField:
    """Create a combobox field."""
    control_props: dict[str, Any] = {
        "placeholder": placeholder or f"Select {label.lower()}",
        "options": [{"id": oid, "value": oval} for oid, oval in options],
    }
    _add_if_set(control_props, "readonly", readonly)
    _add_if_set(control_props, "disabled", disabled)
    return AutoFormField(
        key=key,
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.COMBOBOX,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=_wrapper_props_with_label(label, rules),
            control_props=control_props,
            rules=rules,
        ),
    )


def multiple_combobox_field(
    key: str,
    label: str,
    options: list[tuple[str, str]],
    placeholder: Optional[str] = None,
    rules: Optional[list[ValidationRule]] = None,
    readonly: Optional[bool] = None,
    disabled: Optional[bool] = None,
) -> AutoFormField:
    """Create a multiple combobox field."""
    control_props: dict[str, Any] = {
        "placeholder": placeholder or f"Select {label.lower()}",
        "options": [{"id": oid, "value": oval} for oid, oval in options],
    }
    _add_if_set(control_props, "readonly", readonly)
    _add_if_set(control_props, "disabled", disabled)
    return AutoFormField(
        key=key,
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_control=FieldControl.MULTIPLE_COMBOBOX,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=_wrapper_props_with_label(label, rules),
            control_props=control_props,
            rules=rules,
        ),
    )


def radio_popup_field(
    key: str,
    label: str,
    radio: Optional[dict] = None,
    node_popup: Optional[dict] = None,
    initial_api_config: Optional[dict] = None,
    rules: Optional[list[ValidationRule]] = None,
) -> AutoFormField:
    """Create a radio popup field."""
    control_props: dict[str, Any] = {}
    _add_if_set(control_props, "radio", radio)
    _add_if_set(control_props, "nodePopup", node_popup)
    return AutoFormField(
        key=key,
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.RADIO_POPUP,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=_wrapper_props_with_label(label, rules),
            control_props=control_props if control_props else None,
            rules=rules,
            initial_api_config=initial_api_config,
        ),
    )


def single_upload_field(
    key: str,
    label: str,
    upload_api_config: Optional[dict] = None,
    hint: Optional[str] = None,
    rules: Optional[list[ValidationRule]] = None,
    readonly: Optional[bool] = None,
    disabled: Optional[bool] = None,
) -> AutoFormField:
    """Create a single upload field."""
    control_props: dict[str, Any] = {}
    _add_if_set(control_props, "hint", hint)
    _add_if_set(control_props, "readonly", readonly)
    _add_if_set(control_props, "disabled", disabled)
    _add_if_set(control_props, "uploadApiConfig", upload_api_config)
    return AutoFormField(
        key=key,
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.SINGLE_UPLOAD,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=_wrapper_props_with_label(label, rules),
            control_props=control_props if control_props else None,
            rules=rules,
        ),
    )


def node_popup_field(
    key: str,
    label: str,
    node_popup: Optional[dict] = None,
    rules: Optional[list[ValidationRule]] = None,
) -> AutoFormField:
    """Create a node popup field."""
    control_props: dict[str, Any] = {"label": label}
    _add_if_set(control_props, "nodePopup", node_popup)
    return AutoFormField(
        key=key,
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.NODE_POPUP,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=_wrapper_props_with_label(label, rules),
            control_props=control_props,
            rules=rules,
        ),
    )


def single_upload_field_mapping_field(
    key: str,
    label: str,
    upload_api_config: Optional[dict] = None,
    hint: Optional[str] = None,
    rules: Optional[list[ValidationRule]] = None,
    readonly: Optional[bool] = None,
    disabled: Optional[bool] = None,
) -> AutoFormField:
    """Create a single upload field mapping field."""
    control_props: dict[str, Any] = {}
    _add_if_set(control_props, "hint", hint)
    _add_if_set(control_props, "readonly", readonly)
    _add_if_set(control_props, "disabled", disabled)
    _add_if_set(control_props, "uploadApiConfig", upload_api_config)
    return AutoFormField(
        key=key,
        output_type=OutputType.OBJECT,
        field_config=FieldConfig(
            field_control=FieldControl.SINGLE_UPLOAD_FIELD_MAPPING,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=_wrapper_props_with_label(label, rules),
            control_props=control_props if control_props else None,
            rules=rules,
        ),
    )
