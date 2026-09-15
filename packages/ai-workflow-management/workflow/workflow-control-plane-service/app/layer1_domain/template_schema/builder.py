"""Build the Template Data Editor AutoForm schema from template fields.

Pure function — no IO. Given the governance template's field configuration plus
the BDM id, it emits a single OData-backed editable table whose value-help
columns fetch their rows live from the Integration Hub search-help service
(registered FE service ``integration-hub``). See
``docs/template-data-editor-node.md``.

Search-help rows come back flat as ``{ data: [{ <fieldName>: ... }], count }``,
keyed by the governance API field name (e.g. ``cityName``). Each value-help
column selects a whole row and an ``update_row`` action writes the row's
``<fieldName>`` value into the table column.
"""

from __future__ import annotations

from app.layer1_domain.auto_form_mapper.helpers import (
    INTEGRATION_HUB_SERVICE,
    odata_array_field_with_actions,
    search_help_select_field,
    text_field,
)
from app.layer1_domain.auto_form_mapper.models import AutoFormField, ValidationRule
from app.layer1_domain.template_schema.models import TemplateFieldConfig

# The table artifact key DATA_EDIT's provisioning + PATCH machinery expects.
TABLE_KEY = "table_data"


def build_template_data_edit_schema(
    fields: list[TemplateFieldConfig],
    *,
    template_id: str,
    model_id: str,
    service: str = INTEGRATION_HUB_SERVICE,
    page_size: int = 100,
    readonly: bool = False,
    label: str = "Data Table",
) -> list[AutoFormField]:
    """Return the AutoForm schema (one OData table) for the given fields.

    - Plain fields → ``InputControl`` cells.
    - Value-help fields → ``SelectTableControl`` cells bound to the BDM
      search-help service. Each fires an ``on_select_<field>`` action that
      writes the picked row's ``<field>`` value into the column.
    - Dependent lookups → a ``dependencies`` / ``contextValues`` wiring on the
      child cell (so the child re-queries scoped by the parent's value) and a
      parent action that clears the child when the parent changes.
    """
    ordered = sorted(fields, key=lambda f: f.order)

    # CDM id → column key, so a dependency (expressed in CDM ids) resolves to
    # the sibling column's row key (the alias-based header the file uses).
    cdm_to_key = {f.cdm_id: f.column_key for f in ordered if f.cdm_id}

    columns: list[AutoFormField] = []
    for f in ordered:
        rules = (
            [ValidationRule(method="required", message=f"{f.label} is required")]
            if f.required
            else None
        )
        if f.is_lookup:
            deps = [cdm_to_key.get(c, c) for c in f.dependency_fields]
            context_values = {
                # contextValues keys are CDM ids; values reference the parent
                # column's already-filled scalar value in the current row.
                c: cdm_to_key.get(c, c) for c in f.dependency_fields
            }
            columns.append(
                search_help_select_field(
                    template_id= template_id,
                    key=f.column_key,
                    label=f.label,
                    model_id=model_id,
                    bdm_field=f.cdm_id,
                    page_size=page_size,
                    label_key=f"{f.result_key}",
                    dependencies=deps or None,
                    context_values=context_values or None,
                    value_key=f"{f.result_key}",
                    select_action=f"on_select_{f.field}",
                    rules=rules,
                )
            )
        else:
            columns.append(
                text_field(
                    key=f.column_key,
                    label=f.label,
                    readonly=True if readonly else None,
                    rules=rules,
                )
            )

    actions = _build_actions(ordered)

    table = odata_array_field_with_actions(
        key=TABLE_KEY,
        label=label,
        columns=columns,
        actions=actions or None,
        readonly=readonly,
    )
    return [table]


def _build_actions(
    fields: list[TemplateFieldConfig],
) -> list[dict]:
    """One ``on_select_<field>`` action per value-help column.

    Selecting a search-help row writes that column's own ``<field>`` value
    into the row, and clears any dependent child columns so the user
    re-picks them scoped by the new parent value.
    """
    # CDM id → list of child column keys that depend on it.
    children_of: dict[str, list[tuple[str, str]]] = {}
    for f in fields:
        for c in f.dependency_fields:
            children_of.setdefault(c, []).append((f.field, f.column_key))

    actions: list[dict] = []
    for f in fields:
        if not f.is_lookup:
            continue
        data: dict[str, str] = {f.column_key: f"${{rowData.{f.result_key}}}"}
        # Clear dependents so their search-help re-queries with the new context.
        for child_field, child_key in children_of.get(f.cdm_id, []):
            data[child_key] = f"${{rowData.{child_field}}}"  # set to same value to trigger change detection
        actions.append(
            {
                "action": f"on_select_{f.field}",
                "steps": [{"type": "update_row", "data": data}],
            }
        )
    return actions
