"""Render a NodeSpec into the FE AutoForm schema dicts.

Pure derivation — replaces the hand-written field lists in
``control_flow_schemas.py``. The output is pinned against golden
fixtures (``tests/unit/test_autoform_golden.py``): structurally
identical to the legacy schemas for unchanged node types.

Rendering rules worth calling out:

* ``visible_when`` (positive show-when) is inverted into the FE's
  NOT_EQUALS hide-when ``ConditionConfig``.
* ``required`` renders only for unconditionally visible fields —
  conditional requiredness is a save-time-validation concern; emitting
  it on hidden fields would change FE behavior.
* ``internal`` fields are never rendered.
* ``form_default`` (not the engine ``default``) controls
  ``controlProps.value``; a choices-field with a ``form_default``
  renders with a preselected value and no placeholder (legacy quirk of
  SET_VARIABLE.item_type / OUTPUT.type).
"""

from __future__ import annotations

from typing import Any

from app.layer1_domain.auto_form_mapper import (
    AutoFormField,
    ConditionConfig,
    ConditionOperator,
    FieldConfig,
    FieldControl,
    FieldWrapper,
    OutputType,
    ValidationRule,
    array_field,
    number_field,
    object_field,
    select_field,
    switch_field,
    text_field,
    textarea_field,
)
from app.layer1_domain.node_spec.model import (
    ConfigField,
    FieldType,
    NodeSpec,
    OutputField,
    Widget,
)

_OUTPUT_TYPE_BY_FIELD_TYPE = {
    FieldType.STRING: OutputType.STRING,
    FieldType.NUMBER: OutputType.NUMBER,
    FieldType.BOOLEAN: OutputType.BOOLEAN,
    FieldType.OBJECT: OutputType.OBJECT,
    FieldType.ARRAY: OutputType.ARRAY,
    FieldType.JSON: OutputType.OBJECT,
}

_ARRAY_WRAPPERS = {
    Widget.ARRAY_INLINE: (FieldWrapper.FRAGMENT, FieldWrapper.FORM_ITEM),
    Widget.ARRAY_TABLE: (FieldWrapper.TABLE_ROW, FieldWrapper.TABLE_CELL),
}


def render_input_schema(spec: NodeSpec) -> list[dict[str, Any]]:
    """The palette ``input_schema`` (config form) for a node type."""
    visible = [f for f in spec.config_fields if not f.internal]
    if spec.form_tabs:
        tabs = [
            AutoFormField(
                key=tab.key,
                output_type=OutputType.OBJECT,
                field_config=FieldConfig(
                    field_wrapper=FieldWrapper.TAB_ITEM,
                    wrapper_props={"label": tab.label},
                ),
                fields=[_render_field(f) for f in visible if f.tab == tab.key],
            )
            for tab in spec.form_tabs
        ]
        root = AutoFormField(
            key=spec.form_tabs_key,
            output_type=OutputType.OBJECT,
            field_config=FieldConfig(field_wrapper=FieldWrapper.TAB),
            fields=tabs,
        )
        return [root.to_dict()]
    return [_render_field(f).to_dict() for f in visible]


def render_output_schema(spec: NodeSpec) -> list[dict[str, Any]]:
    return [_render_output(f).to_dict() for f in spec.output_fields]


# ──────────────────────────── config fields ────────────────────────────


def _render_field(f: ConfigField) -> AutoFormField:
    rendered = _render_control(f)
    if f.visible_when is not None:
        if isinstance(f.visible_when.equals, tuple):
            raise NotImplementedError(
                f"{f.key}: tuple visible_when has no NOT_IN form-condition"
            )
        rendered.field_config.condition = ConditionConfig(
            field_key=f.visible_when.field,
            operator=ConditionOperator.NOT_EQUALS,
            condition_value=f.visible_when.equals,
        )
    return rendered


def _rules(f: ConfigField) -> list[ValidationRule] | None:
    # Conditional requiredness is validated at save time only — the
    # served form stays as it always was for visible_when fields.
    if f.required and f.visible_when is None:
        return [ValidationRule(method="required")]
    return None


def _render_control(f: ConfigField) -> AutoFormField:
    if f.field_control:
        # Custom FE control: render a text-style field (label, placeholder,
        # required rule preserved) but delegate the control to the named
        # component registered on the FE Builder. render_props (layout spans)
        # are merged onto the wrapper so a full-span custom control (e.g. the
        # INPUT node's Form Builder) keeps its width.
        rendered = text_field(
            key=f.key, label=f.label,
            placeholder=f.placeholder or None,
            rules=_rules(f),
            default=f.form_default,
        )
        rendered.field_config.field_control = f.field_control
        if f.render_props:
            wrapper_props = dict(rendered.field_config.wrapper_props or {})
            wrapper_props.update(f.render_props)
            rendered.field_config.wrapper_props = wrapper_props
        return rendered
    if f.choices:
        return _render_radio(f) if f.widget == Widget.RADIO else _render_select(f)
    if f.type == FieldType.BOOLEAN or f.widget == Widget.SWITCH:
        return switch_field(
            key=f.key, label=f.label,
            default=bool(f.form_default) if f.form_default is not None else False,
        )
    if f.type == FieldType.NUMBER or f.widget == Widget.NUMBER:
        return number_field(
            key=f.key, label=f.label,
            placeholder=f.placeholder or None,
            rules=_rules(f),
            default=f.form_default,
        )
    if f.widget == Widget.TEXTAREA:
        return textarea_field(
            key=f.key, label=f.label,
            placeholder=f.placeholder or None,
            rules=_rules(f),
        )
    if f.widget == Widget.OBJECT_EDITOR:
        return object_field(
            key=f.key, label=f.label, children=[],
            wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=dict(f.render_props) if f.render_props else None,
        )
    if f.type == FieldType.ARRAY or f.widget in _ARRAY_WRAPPERS or f.item_fields:
        outer, inner = _ARRAY_WRAPPERS.get(
            f.widget, (FieldWrapper.SEQUENCE, FieldWrapper.FRAGMENT),
        )
        return array_field(
            key=f.key, label=f.label,
            item_fields=[_render_field(i) for i in f.item_fields],
            wrapper=outer, item_wrapper=inner,
        )
    return text_field(
        key=f.key, label=f.label,
        placeholder=f.placeholder or None,
        rules=_rules(f),
        default=f.form_default,
    )


def _render_select(f: ConfigField) -> AutoFormField:
    options = [(c.value, c.label) for c in f.choices]
    if f.form_default is None:
        return select_field(
            key=f.key, label=f.label, options=options, rules=_rules(f),
        )
    # Preselected select: value instead of placeholder (legacy shape of
    # SET_VARIABLE.item_type and OUTPUT.type).
    return AutoFormField(
        key=f.key,
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.SELECT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": f.label},
            control_props={
                "options": [{"id": oid, "value": oval} for oid, oval in options],
                "value": f.form_default,
            },
            rules=_rules(f),
        ),
    )


def _render_radio(f: ConfigField) -> AutoFormField:
    """Render a choices field as a radio group (RadioPopupControl) — SA-1239.

    Options are ``{id, value}`` (id = stored canonical value, value = display
    label). The FE RadioControl reads ``value`` as the label (``labelKey``
    defaults to ``"value"``, matching SelectControl), so no valueKey/labelKey
    overrides are emitted.
    """
    rules = _rules(f)
    wrapper_props: dict[str, Any] = {"label": f.label}
    if rules:
        wrapper_props["required"] = True
    control_props: dict[str, Any] = {
        "options": [{"id": c.value, "value": c.label} for c in f.choices],
    }
    if f.form_default is not None:
        control_props["value"] = f.form_default
    return AutoFormField(
        key=f.key,
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.RADIO_POPUP,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=wrapper_props,
            control_props=control_props,
            rules=rules,
        ),
    )


# ──────────────────────────── output fields ────────────────────────────


def _render_output(f: OutputField) -> AutoFormField:
    if f.type == FieldType.NUMBER:
        return number_field(key=f.key, label=f.label, readonly=True)
    if f.type == FieldType.BOOLEAN:
        return switch_field(key=f.key, label=f.label, readonly=True)
    if f.type in (FieldType.OBJECT, FieldType.ARRAY, FieldType.JSON):
        return AutoFormField(
            key=f.key,
            output_type=_OUTPUT_TYPE_BY_FIELD_TYPE[f.type],
            field_config=FieldConfig(
                field_control=FieldControl.INPUT,
                field_wrapper=FieldWrapper.FORM_ITEM,
                wrapper_props={"label": f.label},
                control_props={"readonly": True},
            ),
        )
    return text_field(key=f.key, label=f.label, readonly=True)
