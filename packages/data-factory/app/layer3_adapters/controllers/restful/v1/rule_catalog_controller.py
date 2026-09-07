import hashlib
import json
from typing import Any, Dict, List, Tuple, Type

from fastapi import APIRouter, Request, Response

from app.layer3_adapters.controllers.restful.v1.dtos.rule_spec import (
    RULE_SPECS_BY_CATEGORY,
    ParamSpec,
    RuleSpec,
)

router = APIRouter()

_KIND_BY_TYPES: Dict[Tuple[Type, ...], str] = {
    (str,): "string",
    (int,): "integer",
    (int, float): "number",
    (bool,): "boolean",
    (dict,): "object",
}


def _kind(spec: ParamSpec) -> str:
    if spec.types == (list,):
        if spec.item_type is str:
            return "string[]"
        if spec.item_type is dict:
            return "object[]"
        return "array"
    return _KIND_BY_TYPES.get(tuple(spec.types), "any")


def _param(key: str, spec: ParamSpec, required: bool) -> Dict[str, Any]:
    param: Dict[str, Any] = {"key": key, "kind": _kind(spec), "required": required}
    if spec.choices:
        param["choices"] = list(spec.choices)
    if spec.min_value is not None:
        param["minValue"] = spec.min_value
    if spec.describe:
        param["describe"] = spec.describe
    return param


GROUPS = ("SCHEMA", "TRANSFORM", "SHAPE", "VALIDATE", "DEDUP")

_PRESENTATION: Dict[Tuple[str, str], Tuple[str, str, str, str]] = {
    ("VALIDATION", "assignment"): (
        "VALIDATE", "Assign from mapping", "polars",
        "Judge a target column against a mapping table.",
    ),
    ("VALIDATION", "filter"): (
        "VALIDATE", "Filter against mapping", "polars",
        "Keep only rows the mapping table allows.",
    ),
    ("VALIDATION", "length"): (
        "VALIDATE", "Length", "polars", "Value length must sit in range.",
    ),
    ("VALIDATION", "pattern"): (
        "VALIDATE", "Pattern", "polars", "Value must match a regular expression.",
    ),
    ("VALIDATION", "range"): (
        "VALIDATE", "Range", "polars", "Number must sit between two bounds.",
    ),
    ("VALIDATION", "date_range"): (
        "VALIDATE", "Date range", "polars", "Date must sit between two dates.",
    ),
    ("VALIDATION", "allowed_values"): (
        "VALIDATE", "Allowed values", "polars", "Value must be one of a fixed set.",
    ),
    ("VALIDATION", "compare_fields"): (
        "VALIDATE", "Compare fields", "polars", "Compare two columns of the same row.",
    ),
    ("VALIDATION", "conditional_required"): (
        "VALIDATE", "Conditional required", "sandbox",
        "Required only when a condition holds.",
    ),
    ("VALIDATION", "required"): (
        "VALIDATE", "Required", "polars", "The column must carry a value.",
    ),
    ("VALIDATION", "expression"): (
        "VALIDATE", "Expression", "sandbox", "A whitelisted expression must hold.",
    ),
    ("VALIDATION", "reference_lookup"): (
        "VALIDATE", "Reference lookup", "polars",
        "Value must be present in, or absent from, a reference set.",
    ),
    ("VALIDATION", "unique"): (
        "DEDUP", "Unique", "polars", "The column must not repeat.",
    ),
    ("VALIDATION", "set_unique"): (
        "DEDUP", "Set unique", "polars", "A column combination must not repeat.",
    ),
    ("VALIDATION", "fuzzy_unique"): (
        "DEDUP", "Fuzzy unique", "polars",
        "Unique after the formatting noise is stripped.",
    ),
    ("VALIDATION", "composite_unique"): (
        "DEDUP", "Composite unique", "polars",
        "Unique across a column combination, each with its own match mode.",
    ),
    ("VALIDATION", "semantic_unique"): (
        "DEDUP", "Semantic unique", "semantic",
        "Unique by meaning, compared against an embedding reference.",
    ),
    ("TRANSFORMATION", "filter"): (
        "TRANSFORM", "Filter rows", "sandbox", "Keep only the rows an expression selects.",
    ),
    ("TRANSFORMATION", "mapping"): (
        "TRANSFORM", "Derive column", "sandbox",
        "Compute a new column from an expression.",
    ),
    ("TRANSFORMATION", "case_when_expr"): (
        "TRANSFORM", "Conditional", "sandbox",
        "Branch on conditions to fill one column.",
    ),
    ("TRANSFORMATION", "insert_row"): (
        "TRANSFORM", "Insert row", "sandbox", "Add a row at an identified position.",
    ),
    ("TRANSFORMATION", "delete_row"): (
        "TRANSFORM", "Delete row", "sandbox", "Remove the rows an identifier selects.",
    ),
    ("TRANSFORMATION", "update_row"): (
        "TRANSFORM", "Update row", "sandbox", "Rewrite the rows an identifier selects.",
    ),
    ("TRANSFORMATION", "drop_columns"): (
        "SCHEMA", "Drop columns", "polars", "Remove the named columns.",
    ),
    ("TRANSFORMATION", "rename_columns"): (
        "SCHEMA", "Rename columns", "polars", "Rename source columns to canonical names.",
    ),
    ("SCHEMA_TRANSFORMATION", "select_columns"): (
        "SCHEMA", "Select columns", "polars", "Keep only the named columns.",
    ),
    ("SCHEMA_TRANSFORMATION", "reorder_columns"): (
        "SCHEMA", "Reorder columns", "polars", "Fix the column order for a positional target.",
    ),
    ("SCHEMA_TRANSFORMATION", "rename_columns"): (
        "SCHEMA", "Rename columns", "polars", "Rename source columns to canonical names.",
    ),
    ("SCHEMA_TRANSFORMATION", "drop_columns"): (
        "SCHEMA", "Drop columns", "polars", "Remove the named columns.",
    ),
    ("SCHEMA_TRANSFORMATION", "cast"): (
        "TRANSFORM", "Cast type", "polars", "Coerce a column to a target type.",
    ),
    ("SCHEMA_TRANSFORMATION", "mapping"): (
        "TRANSFORM", "Derive column", "sandbox",
        "Compute a new column from an expression.",
    ),
    ("SCHEMA_TRANSFORMATION", "combine_columns"): (
        "TRANSFORM", "Concatenate", "polars", "Join several columns into one.",
    ),
    ("SCHEMA_TRANSFORMATION", "split_column"): (
        "TRANSFORM", "Split column", "polars", "Split one column into several.",
    ),
    ("SCHEMA_TRANSFORMATION", "derive"): (
        "TRANSFORM", "Derive from condition", "polars",
        "Set columns when a condition holds.",
    ),
    ("SCHEMA_TRANSFORMATION", "join_reference"): (
        "TRANSFORM", "Join reference", "polars",
        "Bring columns in from a reference table or file.",
    ),
    ("SCHEMA_TRANSFORMATION", "add_technical_fields"): (
        "TRANSFORM", "Add technical fields", "polars",
        "Add the platform's bookkeeping columns.",
    ),
    ("SCHEMA_TRANSFORMATION", "add_row_index"): (
        "TRANSFORM", "Add row index", "polars", "Number the rows.",
    ),
    ("SCHEMA_TRANSFORMATION", "explode"): (
        "SHAPE", "Explode list", "polars", "One row per element of a list column.",
    ),
    ("SCHEMA_TRANSFORMATION", "unnest"): (
        "SHAPE", "Unnest struct", "polars", "Lift struct members to columns.",
    ),
    ("SCHEMA_TRANSFORMATION", "flatten_struct"): (
        "SHAPE", "Flatten struct", "polars", "Flatten a nested struct into flat columns.",
    ),
    ("SCHEMA_TRANSFORMATION", "build_nested_json"): (
        "SHAPE", "Build nested JSON", "polars", "Fold flat columns into a nested document.",
    ),
    ("SCHEMA_TRANSFORMATION", "nest_children"): (
        "SHAPE", "Nest children", "polars", "Nest child rows under their parent.",
    ),
}


def _presentation(category: str, rule_type: str) -> Tuple[str, str, str, str]:
    try:
        return _PRESENTATION[(category, rule_type)]
    except KeyError:
        raise RuntimeError(
            f"rule type {category}/{rule_type} has no catalog metadata; "
            "add it to _PRESENTATION"
        ) from None


def _entry(category: str, rule_type: str, spec: RuleSpec) -> Dict[str, Any]:
    keys = list(spec.required) + [k for k in spec.optional if k not in spec.required]
    for key in spec.params:
        if key not in keys:
            keys.append(key)

    group, label, engine, does = _presentation(category, rule_type)

    return {
        "category": category,
        "type": rule_type,
        "group": group,
        "label": label,
        "engine": engine,
        "does": does,
        "oneOf": [list(group_keys) for group_keys in spec.one_of],
        "params": [
            _param(key, spec.params.get(key) or ParamSpec(), key in spec.required)
            for key in keys
        ],
    }


def _build() -> Tuple[Dict[str, Any], str]:
    declared = {
        (category, rule_type)
        for category, specs in RULE_SPECS_BY_CATEGORY.items()
        for rule_type in specs
    }
    orphans = sorted(str(key) for key in set(_PRESENTATION) - declared)
    if orphans:
        raise RuntimeError(f"_PRESENTATION describes rule types no spec declares: {orphans}")

    types: List[Dict[str, Any]] = []
    for category, specs in RULE_SPECS_BY_CATEGORY.items():
        for rule_type in sorted(specs):
            types.append(_entry(category, rule_type, specs[rule_type]))

    types.sort(key=lambda entry: (GROUPS.index(entry["group"]), entry["label"]))

    digest = hashlib.sha1(
        json.dumps(types, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"version": digest, "types": types}, digest


_CATALOG, _VERSION = _build()
_ETAG = f'"{_VERSION}"'


@router.get("")
async def get_rule_types(request: Request, response: Response):
    if request.headers.get("if-none-match") == _ETAG:
        return Response(status_code=304, headers={"ETag": _ETAG})

    response.headers["ETag"] = _ETAG
    response.headers["Cache-Control"] = "no-cache"
    return _CATALOG
