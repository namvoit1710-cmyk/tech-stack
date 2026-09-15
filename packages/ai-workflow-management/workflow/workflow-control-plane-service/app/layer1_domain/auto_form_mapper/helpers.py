"""Convenience builder functions for common field patterns."""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import quote

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


def _table_columns(columns: list[AutoFormField]) -> list[AutoFormField]:
    """Wrap each column field in TableCellWrapper (preserves control/rules)."""
    out: list[AutoFormField] = []
    for col in columns:
        cfg = col.field_config
        out.append(AutoFormField(
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
    return out


def table_array_field(
    key: str,
    label: str,
    columns: list[AutoFormField],
) -> AutoFormField:
    """Create a table array field. Columns auto-wrapped in TableCellWrapper."""
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
                fields=_table_columns(columns),
            ),
        ],
    )


def odata_array_field(
    key: str,
    label: str,
    columns: list[AutoFormField],
) -> AutoFormField:
    """Create an OData-backed array field for DATA_EDIT.

    Same row/cell structure as ``table_array_field`` but the outer wrapper
    is ``OdataWrapper`` so the FE drives rows from OData queries instead
    of inline values.
    """
    return AutoFormField(
        key=key,
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_wrapper=FieldWrapper.TABLE_ODATA,
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
                fields=_table_columns(columns),
            ),
        ],
    )


# Registered FE service name for the Integration Hub (see autoform skill §11 —
# the registered service key.
INTEGRATION_HUB_SERVICE = "integration-hub"


def _encode_context_values(context_values: Optional[dict[str, str]]) -> str:
    """URL-encode dependent-lookup context as the ``contextValues`` query param.

    The key is the BDM context field, the value is the dependent form value, e.g.
    ``{"country_1": "AD"}`` -> ``%7B%22country_1%22%3A%20%22AD%22%7D``. Governance
    expects ``contextValues`` as a JSON string (see ``SearchHelpRowsQueryDto``).
    """
    return quote(json.dumps(context_values or {}))


def search_help_select_field(
    key: str,
    label: str,
    *,
    template_id: str,
    model_id: str,
    bdm_field: str,
    page_size: int = 100,
    value_key: str = "rowKey",
    label_key: Optional[str] = None,
    dependencies: Optional[list[str]] = None,
    context_values: Optional[dict[str, str]] = None,
    select_action: Optional[str] = None,
    multiple: bool = False,
    rules: Optional[list[ValidationRule]] = None,
) -> AutoFormField:
    """Create a value-help ``SelectTableControl`` backed by Integration Hub.

    The control's rows are fetched live from the BDM search-help endpoint via
    the registered ``integration-hub`` service (which prepends the base URL +
    ``api/v1`` basePath — so ``endpoint`` is the path *after* ``api/v1``)::

        /business-data-models/{model_id}/bdm-fields/{bdm_field}/search-help/rows

    The endpoint returns flat rows — ``{ data: [{ <fieldName>: ... }], count }``
    keyed by the governance API field name (e.g. ``cityName``) — so
    ``responseMapping`` extracts ``data`` / ``count`` and ``value_key`` /
    ``label_key`` point directly at the field name (no ``payload`` nesting).

    ``dependencies`` / ``context_values`` wire dependent lookups (e.g. *city*
    depends on *country*): the FE re-queries the child when a parent changes,
    passing ``contextValues`` such as ``{"country_1": "Country"}`` (the value
    is the parent column key the FE watches). ``select_action`` names the
    ``OdataWrapper`` action fired on row select (its ``update_row`` step writes
    the picked row's ``<fieldName>`` value into the row column).
    """
    endpoint = (
        f"/canonical-data-models/{model_id}"
        f"/cdm-fields/{bdm_field}/search-help/rows"
    )
    server_options: dict[str, Any] = {
        "service": "governance",
        "method": "GET",
        "endpoint": endpoint,
        "queryParams": {
            "templateId": template_id,
            "$top": 10,
            "$skip": 0,
            "$count": True,
            "$orderby": "rowSeq asc",
            "contextValues": _encode_context_values(context_values) if context_values else None,
        },
        "responseMapping": {"dataPath": "data", "totalPath": "count"},
    }
    control_props: dict[str, Any] = {
        "valueKey": value_key,
        "labelKey": label_key or f"{key}",
        "serverOptions": server_options,
    }
    if multiple:
        control_props["multiple"] = True
    if select_action:
        control_props["selectAction"] = select_action

    return AutoFormField(
        key=key,
        output_type=OutputType.ARRAY if multiple else OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.SELECT_TABLE,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=_wrapper_props_with_label(label, rules),
            control_props=control_props,
            rules=rules,
        ),
    )


def odata_array_field_with_actions(
    key: str,
    label: str,
    columns: list[AutoFormField],
    *,
    actions: Optional[list[dict[str, Any]]] = None,
    readonly: bool = False,
    show_search: bool = True,
) -> AutoFormField:
    """OData-backed array field that also carries ``OdataWrapper`` actions.

    Same row/cell structure as :func:`odata_array_field` but the outer
    ``wrapperProps`` exposes add/edit/delete toggles plus an ``actions`` list
    (the ``on_select_*`` / ``update_row`` steps used by dependent lookups).
    """
    wrapper_props: dict[str, Any] = {"label": label, "isShowSearch": show_search}
    if readonly:
        wrapper_props["readonly"] = True
    else:
        wrapper_props.update({"isAdd": True, "isEdit": True, "isDelete": True})
    if actions:
        wrapper_props["actions"] = actions

    return AutoFormField(
        key=key,
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_wrapper=FieldWrapper.TABLE_ODATA,
            wrapper_props=wrapper_props,
        ),
        fields=[
            AutoFormField(
                key="0",
                output_type=OutputType.OBJECT,
                field_config=FieldConfig(
                    field_wrapper=FieldWrapper.TABLE_ROW,
                    wrapper_props={"isAdd": True, "isDelete": True},
                ),
                fields=_table_columns(columns),
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
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.CODE_CONTROL,
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
    _add_if_set(control_props, "labelSpan", "xl-12 lg-12 md-12 xs-12")
    _add_if_set(control_props, "fieldSpan", "xl-12 lg-12 md-12 xs-12")
    _add_if_set(control_props, "className", "w-full")
    return AutoFormField(
        key=key,
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.SINGLE_UPLOAD,
            field_wrapper=FieldWrapper.FORM_ITEM,
            # wrapper_props=_wrapper_props_with_label(label, rules),
            wrapper_props={
                "labelSpan": "xl-12 lg-12 md-12 xs-12",
                "fieldSpan": "xl-12 lg-12 md-12 xs-12",
                "label": "Input Schema"
            },

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
            field_control=FieldControl.SINGLE_UPLOAD,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=_wrapper_props_with_label(label, rules),
            control_props=control_props if control_props else None,
            rules=rules,
        ),
    )
