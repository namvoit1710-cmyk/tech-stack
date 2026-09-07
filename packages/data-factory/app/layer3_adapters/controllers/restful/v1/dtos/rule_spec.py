"""Declared shapes for every rule type, and a checker the DTOs run at the edge.

A rule the engine does not recognise used to be logged and skipped, so a run with
a typo'd rule type — or a typo'd param key, which is far likelier — came back
``200`` with zero violations. A validation that silently checks nothing is worse
than one that fails: nobody goes looking for the rule that never ran.

So the boundary rejects what it cannot execute. Every rule type is declared here
with its required and optional params; anything unknown, missing, or of the wrong
type is a 422 naming the rule and the offending key.

Adding a rule type means adding a spec here as well as a branch in the engine.
That duplication is deliberate: it is what makes the boundary able to say no.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Type

from app.layer4_frameworks.providers.validation.semantic_match import (
    SemanticRuleError,
    SemanticSpec,
)
from app.layer4_frameworks.providers.conditions import (
    ALIASES,
    OPERATORS,
    canonical_operator,
)
from app.layer4_frameworks.providers.expressions.safe_expression import (
    RuleExpressionError,
    compile_expression,
)


@dataclass(frozen=True)
class ParamSpec:
    """One parameter of one rule type."""

    types: Tuple[Type, ...] = (object,)
    #: For list params: the type every element must have.
    item_type: Optional[Type] = None
    #: For string params: the permitted values, lower-cased.
    choices: Optional[Tuple[str, ...]] = None
    min_value: Optional[float] = None
    describe: str = ""


@dataclass(frozen=True)
class RuleSpec:
    required: Tuple[str, ...] = ()
    optional: Tuple[str, ...] = ()
    params: Mapping[str, ParamSpec] = field(default_factory=dict)
    #: Groups where at least one member must be present, e.g. column|columns.
    one_of: Tuple[Tuple[str, ...], ...] = ()


_COLUMN_LIST = ParamSpec(types=(list,), item_type=str, describe="list of column names")
_NAME = ParamSpec(types=(str,), describe="column name")
_FLAG = ParamSpec(types=(bool,), describe="true/false")
_TEXT = ParamSpec(types=(str,), describe="text")
_FILE = ParamSpec(types=(str,), describe="file id or path")

# --------------------------------------------------------------------------
# Validation rules — must match polars_validator_provider / rule_handlers.
# --------------------------------------------------------------------------
VALIDATION_RULE_SPECS: Dict[str, RuleSpec] = {
    "assignment": RuleSpec(
        required=("source_column", "target_column", "mapping_source"),
        optional=("mapping_format", "mapping_source_column",
                  "mapping_target_column", "mode"),
        params={"source_column": _NAME, "target_column": _NAME,
                "mapping_source": _FILE, "mapping_format": _TEXT,
                "mapping_source_column": _NAME, "mapping_target_column": _NAME,
                "mode": ParamSpec(types=(str,), choices=("match", "allowed"),
                                  describe="how the target is judged")},
    ),
    "filter": RuleSpec(
        required=("source_column", "target_column", "mapping_source"),
        optional=("mapping_format", "mapping_source_column",
                  "mapping_target_column", "severity"),
        params={"source_column": _NAME, "target_column": _NAME,
                "mapping_source": _FILE, "mapping_format": _TEXT,
                "mapping_source_column": _NAME, "mapping_target_column": _NAME,
                "severity": ParamSpec(types=(str,),
                                      choices=("error", "warning", "info"),
                                      describe="how hard the violation lands")},
    ),
    # -- declarative types: the common half of `expression`, lifted out ------
    "length": RuleSpec(
        required=("columns",),
        optional=("min", "max"),
        params={"columns": _COLUMN_LIST,
                "min": ParamSpec(types=(int,), min_value=0, describe="characters"),
                "max": ParamSpec(types=(int,), min_value=0, describe="characters")},
        one_of=(("min", "max"),),
    ),
    "pattern": RuleSpec(
        required=("columns", "pattern"),
        optional=("full_match",),
        params={"columns": _COLUMN_LIST, "pattern": _TEXT, "full_match": _FLAG},
    ),
    "range": RuleSpec(
        required=("columns",),
        optional=("min", "max", "exclusive"),
        params={"columns": _COLUMN_LIST,
                "min": ParamSpec(types=(int, float), describe="number"),
                "max": ParamSpec(types=(int, float), describe="number"),
                "exclusive": _FLAG},
        one_of=(("min", "max"),),
    ),
    "date_range": RuleSpec(
        required=("columns",),
        optional=("after", "before", "format"),
        params={"columns": _COLUMN_LIST, "after": _TEXT, "before": _TEXT,
                "format": _TEXT},
        one_of=(("after", "before"),),
    ),
    "allowed_values": RuleSpec(
        required=("columns", "values"),
        optional=("case_sensitive",),
        params={"columns": _COLUMN_LIST,
                "values": ParamSpec(types=(list,), describe="the permitted values"),
                "case_sensitive": _FLAG},
    ),
    "compare_fields": RuleSpec(
        required=("left", "right", "operator"),
        optional=("numeric",),
        params={"left": _NAME, "right": _NAME,
                "operator": ParamSpec(types=(str,),
                                      choices=("eq", "ne", "lt", "le", "gt", "ge"),
                                      describe="comparison"),
                "numeric": _FLAG},
    ),
    # `when` is one condition, or {match: ALL|ANY, conditions: [...]} -- the same
    # grammar `derive` takes, and the shape SMDG's MandatoryRule needs to express
    # a `whenLogicalExpression` over several SOURCE conditions.
    "conditional_required": RuleSpec(
        required=("when", "then"),
        params={"when": ParamSpec(types=(dict,),
                                  describe="{column, operator, value} or "
                                           "{match, conditions:[...]}"),
                "then": _COLUMN_LIST},
    ),
    "required": RuleSpec(
        required=("columns",),
        params={"columns": _COLUMN_LIST},
    ),
    "expression": RuleSpec(
        required=("expression",),
        optional=("columns",),
        params={"expression": _TEXT, "columns": _COLUMN_LIST},
    ),
    "unique": RuleSpec(
        required=("columns",),
        params={"columns": _COLUMN_LIST},
    ),
    "set_unique": RuleSpec(
        required=("columns",),
        params={"columns": _COLUMN_LIST},
    ),
    # `unique` with the formatting noise stripped first: ' ACME Ltd.' and 'acme ltd'
    # are one vendor typed twice, and an exact check reports neither. `method` decides
    # how much noise to ignore, so a wrong value is refused here rather than quietly
    # falling back to a stricter comparison than the rule author asked for.
    "fuzzy_unique": RuleSpec(
        required=("columns",),
        optional=("method",),
        params={"columns": _COLUMN_LIST,
                "method": ParamSpec(
                    types=(str,),
                    choices=("normalized", "digits", "trimmed"),
                    describe="which differences to treat as the same value")},
    ),
    # `unique` by meaning rather than by string. `fuzzy_unique` collapses formatting
    # and then compares keys, so a differently-spelled word produces a different key
    # and neither row is reported. This compares embeddings in the database instead.
    # Every part of `reference` is named by the caller - the engine carries no
    # tenant table of its own.
    # An AND of several fields, each matched exactly or loosely. This is the shape
    # SMDG's DuplicationCheck groups actually use - the group ANDs its fields and
    # each field carries its own isFuzzySearch - and neither `set_unique` (exact
    # composite) nor `fuzzy_unique` (loose, single column) could express it.
    "composite_unique": RuleSpec(
        required=("columns",),
        params={"columns": ParamSpec(
            types=(list,),
            describe="column names, or {column, match: exact|fuzzy|semantic} "
                     "objects; a semantic component may carry its own threshold "
                     "and reference")},
    ),
    "semantic_unique": RuleSpec(
        required=("columns",),
        optional=("threshold", "min_length", "model", "reference"),
        params={
            "columns": _COLUMN_LIST,
            "threshold": ParamSpec(types=(int, float), min_value=0,
                                   describe="cosine similarity, 0 < t <= 1"),
            "min_length": ParamSpec(types=(int,), min_value=1,
                                    describe="shorter values carry too little signal"),
            "model": _TEXT,
            "reference": ParamSpec(types=(dict,),
                                   describe="{schema, table, column, key_column?, "
                                            "embedding_column?, filter?}"),
        },
    ),
    "reference_lookup": RuleSpec(
        optional=(
            "columns", "source_columns", "key_columns", "key_column", "field",
            "reference_source", "reference_id", "reference_format", "violate_when",
            "separator",
        ),
        params={
            "columns": _COLUMN_LIST,
            "source_columns": _COLUMN_LIST,
            "key_columns": _COLUMN_LIST,
            "key_column": _NAME,
            "field": _NAME,
            "reference_source": _FILE,
            "reference_id": _FILE,
            "reference_format": _TEXT,
            "separator": _TEXT,
            "violate_when": ParamSpec(
                types=(str,), choices=("in_set", "not_in_set"),
                describe="in_set or not_in_set",
            ),
        },
        one_of=(("reference_source", "reference_id"), ("columns", "source_columns")),
    ),
}

# --------------------------------------------------------------------------
# Schema-transform rules — must match PolarsSchemaTransformerProvider._apply_rules.
# --------------------------------------------------------------------------
_DTYPES = (
    "string", "utf8", "text", "int", "int64", "integer", "float", "float64",
    "double", "bool", "boolean", "decimal", "number", "date", "yyyy-mm-dd",
    "datetime", "timestamp",
)

SCHEMA_TRANSFORM_RULE_SPECS: Dict[str, RuleSpec] = {
    "add_technical_fields": RuleSpec(
        optional=("object_id", "req_id", "task_id", "status", "modify_type",
                  "table_name", "parent_table", "parent_key", "item_id",
                  "source_row", "source_row_offset", "item_id_offset", "names"),
        params={"object_id": _TEXT, "req_id": _TEXT, "task_id": _TEXT,
                "status": _TEXT, "modify_type": _TEXT, "table_name": _TEXT,
                "parent_table": _TEXT, "parent_key": _TEXT,
                "item_id": _NAME, "source_row": _NAME,
                "source_row_offset": ParamSpec(types=(int,), min_value=0,
                                               describe="first row number"),
                "item_id_offset": ParamSpec(types=(int,), min_value=0,
                                            describe="first id"),
                "names": ParamSpec(types=(dict,),
                                   describe="{field: column name} overrides")},
    ),
    "rename_columns": RuleSpec(
        required=("mapping",),
        params={"mapping": ParamSpec(types=(dict,), describe="{old: new}")},
    ),
    "drop_columns": RuleSpec(
        optional=("columns", "columns_to_drop"),
        params={"columns": _COLUMN_LIST, "columns_to_drop": _COLUMN_LIST},
        one_of=(("columns", "columns_to_drop"),),
    ),
    "select_columns": RuleSpec(required=("columns",), params={"columns": _COLUMN_LIST}),
    "reorder_columns": RuleSpec(required=("columns",), params={"columns": _COLUMN_LIST}),
    "cast": RuleSpec(
        optional=("column", "columns", "dtype", "format", "strict"),
        params={
            "column": _NAME,
            "columns": _COLUMN_LIST,
            "dtype": ParamSpec(types=(str,), choices=_DTYPES, describe="a supported dtype"),
            "format": _TEXT,
            "strict": _FLAG,
        },
        one_of=(("column", "columns"),),
    ),
    "explode": RuleSpec(required=("column",), params={"column": _NAME}),
    "unnest": RuleSpec(required=("column",), params={"column": _NAME}),
    "flatten_struct": RuleSpec(
        required=("column",), optional=("prefix",),
        params={"column": _NAME, "prefix": _TEXT},
    ),
    "mapping": RuleSpec(
        required=("expression", "new_col"),
        params={"expression": _TEXT, "new_col": _NAME},
    ),
    "combine_columns": RuleSpec(
        required=("target", "sources"), optional=("mode", "separator"),
        params={
            "target": _NAME, "sources": _COLUMN_LIST, "separator": _TEXT,
            "mode": ParamSpec(types=(str,), choices=("join", "sum"), describe="join or sum"),
        },
    ),
    "split_column": RuleSpec(
        required=("source", "targets"), optional=("delimiter",),
        params={"source": _NAME, "targets": _COLUMN_LIST, "delimiter": _TEXT},
    ),
    "build_nested_json": RuleSpec(
        optional=("array_paths", "target_columns", "root_object"),
        params={
            "array_paths": _COLUMN_LIST,
            "target_columns": _COLUMN_LIST,
            "root_object": _TEXT,
        },
    ),
    "add_row_index": RuleSpec(
        optional=("name", "offset"),
        params={"name": _NAME, "offset": ParamSpec(types=(int,), min_value=0)},
    ),
    "join_reference": RuleSpec(
        required=("on",),
        optional=(
            "file_id", "file_path", "table", "columns", "prefix", "how",
            "file_format", "version_id", "sheet_names", "header_row",
        ),
        params={
            "on": _COLUMN_LIST,
            "file_id": _FILE,
            "file_path": _FILE,
            "table": _NAME,
            "columns": _COLUMN_LIST,
            "prefix": _TEXT,
            "file_format": _TEXT,
            "version_id": _TEXT,
            "sheet_names": _COLUMN_LIST,
            "header_row": ParamSpec(types=(int,), min_value=0),
            "how": ParamSpec(
                types=(str,), choices=("left", "inner", "outer", "semi", "anti", "full"),
                describe="a polars join strategy",
            ),
        },
        one_of=(("file_id", "file_path", "table"),),
    ),
    "derive": RuleSpec(
        required=("when", "then"),
        optional=("only_when_empty",),
        params={
            "when": ParamSpec(types=(dict,), describe="a condition object"),
            "then": ParamSpec(types=(list,), item_type=dict,
                              describe="list of {column, value}"),
            "only_when_empty": _FLAG,
        },
    ),
    "nest_children": RuleSpec(
        required=("on", "children"),
        params={
            "on": _COLUMN_LIST,
            "children": ParamSpec(types=(list,), item_type=dict,
                                  describe="list of child descriptors"),
        },
    ),
}

_IDENTIFIER_TYPE = ParamSpec(
    types=(str,), choices=("index", "condition", "values"),
    describe="how the row is found",
)
_ROW_INDEX = ParamSpec(types=(int,), min_value=0, describe="0-based row index")
_MATCH_VALUES = ParamSpec(types=(dict,), describe="{column: value} the row must carry")
_ROW_DATA = ParamSpec(types=(dict,), describe="{column: value} to write")
_ROW_IDENTIFIER_PARAMS = {
    "identifier_type": _IDENTIFIER_TYPE,
    "index": _ROW_INDEX,
    "expression": _TEXT,
    "match_values": _MATCH_VALUES,
}
_ROW_IDENTIFIER_ONE_OF = (("index", "expression", "match_values"),)

TRANSFORMATION_RULE_SPECS: Dict[str, RuleSpec] = {
    "filter": RuleSpec(
        required=("expression",),
        params={"expression": _TEXT},
    ),
    "mapping": RuleSpec(
        required=("expression", "new_col"),
        params={"expression": _TEXT, "new_col": _NAME},
    ),
    "drop_columns": RuleSpec(
        required=("columns_to_drop",),
        params={"columns_to_drop": _COLUMN_LIST},
    ),
    "rename_columns": RuleSpec(
        required=("mapping",),
        params={"mapping": ParamSpec(types=(dict,), describe="{old: new}")},
    ),
    "case_when_expr": RuleSpec(
        required=("column_dest", "conditions"),
        optional=("otherwise",),
        params={
            "column_dest": _NAME,
            "conditions": ParamSpec(types=(list,), item_type=dict,
                                    describe="list of {when_expression, result}"),
            "otherwise": ParamSpec(describe="the value used when nothing matched"),
        },
    ),
    "insert_row": RuleSpec(
        required=("identifier_type",),
        optional=("index", "expression", "match_values", "data", "position",
                  "use_column_indices"),
        params={
            **_ROW_IDENTIFIER_PARAMS,
            "data": _ROW_DATA,
            "position": ParamSpec(types=(str,),
                                  choices=("before", "after", "at_index"),
                                  describe="where the new row lands"),
            "use_column_indices": _FLAG,
        },
        one_of=_ROW_IDENTIFIER_ONE_OF,
    ),
    "delete_row": RuleSpec(
        required=("identifier_type",),
        optional=("index", "expression", "match_values"),
        params=dict(_ROW_IDENTIFIER_PARAMS),
        one_of=_ROW_IDENTIFIER_ONE_OF,
    ),
    "update_row": RuleSpec(
        required=("identifier_type",),
        optional=("index", "expression", "match_values", "data", "allow_multiple",
                  "use_column_indices"),
        params={
            **_ROW_IDENTIFIER_PARAMS,
            "data": _ROW_DATA,
            "allow_multiple": _FLAG,
            "use_column_indices": _FLAG,
        },
        one_of=_ROW_IDENTIFIER_ONE_OF,
    ),
}

#: Re-exported from the one place the engine reads them, so the boundary cannot
#: come to accept an operator the engine has dropped, or refuse one it has added.
#: `ALIASES` carries `conditional_required`'s original lower-case spelling.
DERIVE_OPERATORS = OPERATORS


class RuleSpecError(ValueError):
    """Raised with every problem found, so one round trip fixes the whole payload."""


def _type_name(types: Sequence[Type]) -> str:
    friendly = {str: "string", int: "integer", float: "number", bool: "boolean",
                list: "list", dict: "object"}
    return " or ".join(friendly.get(t, getattr(t, "__name__", str(t))) for t in types)


def _check_param(where: str, key: str, value: Any, spec: ParamSpec, problems: List[str]) -> None:
    # bool is an int subclass; an integer param must not silently accept True.
    if int in spec.types and bool not in spec.types and isinstance(value, bool):
        problems.append(f"{where}: '{key}' must be {_type_name(spec.types)}, got boolean")
        return

    if spec.types != (object,) and not isinstance(value, spec.types):
        problems.append(
            f"{where}: '{key}' must be {_type_name(spec.types)}, "
            f"got {type(value).__name__}"
        )
        return

    if spec.item_type is not None and isinstance(value, list):
        bad = [
            index for index, item in enumerate(value)
            if not isinstance(item, spec.item_type)
        ]
        if bad:
            problems.append(
                f"{where}: '{key}' must contain only "
                f"{_type_name((spec.item_type,))} values; "
                f"wrong type at index {bad[0]}"
            )
            return

    if spec.choices is not None and isinstance(value, str):
        if value.strip().lower() not in spec.choices:
            problems.append(
                f"{where}: '{key}' must be one of {', '.join(spec.choices)}; got '{value}'"
            )
            return

    if spec.min_value is not None and isinstance(value, (int, float)):
        if value < spec.min_value:
            problems.append(f"{where}: '{key}' must be >= {spec.min_value}; got {value}")


def _check_derive_condition(where: str, when: Any, problems: List[str]) -> None:
    """A malformed ``when`` makes the whole derivation a no-op, so check it here."""
    if not isinstance(when, dict):
        return
    conditions = when.get("conditions")
    if conditions is None:
        conditions = [when]
    elif not isinstance(conditions, list):
        problems.append(f"{where}: 'when.conditions' must be a list")
        return

    match = when.get("match")
    if match is not None and str(match).strip().upper() not in ("ALL", "ANY"):
        problems.append(f"{where}: 'when.match' must be ALL or ANY; got '{match}'")

    for index, condition in enumerate(conditions):
        label = f"{where}: when.conditions[{index}]" if len(conditions) > 1 else f"{where}: when"
        if not isinstance(condition, dict):
            problems.append(f"{label} must be an object")
            continue
        if not (condition.get("column") or condition.get("field")):
            problems.append(f"{label} needs a 'column'")
        operator = condition.get("operator")
        # `canonical_operator`, not an upper-case membership test: the engine
        # accepts `eq` as `EQUAL`, and a boundary that spelled the check itself
        # would reject every payload written before the two grammars merged.
        if operator is not None and canonical_operator(operator) is None:
            problems.append(
                f"{label}: unknown operator '{operator}'. "
                f"Supported: {', '.join(DERIVE_OPERATORS)} "
                f"(or {', '.join(sorted(ALIASES))})"
            )


#: Params whose value is a rule expression, wherever they appear.
_EXPRESSION_PARAMS = ("expression", "when_expression")


def _check_expression_params(where: str, params: Mapping[str, Any], problems: List[str]) -> None:
    """Compile every expression in the payload, here, at the boundary.

    An expression that cannot compile used to be caught at run time, logged,
    and skipped — so the caller got HTTP 200 and zero violations for a rule
    that never ran. And an expression that reaches outside polars should never
    reach the engine at all.
    """
    for key in _EXPRESSION_PARAMS:
        value = params.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            continue  # the type check above already reported this
        try:
            compile_expression(value, columns=params.get("columns") or [])
        except RuleExpressionError as exc:
            problems.append(f"{where}: '{key}' is not usable — {exc}")


def validate_rules(
    rules: Any, specs: Mapping[str, RuleSpec], *, label: str = "rules"
) -> Any:
    """Check a rule list against its registry. Raises ``RuleSpecError`` listing
    every problem, so a caller fixes the whole payload in one go."""
    if rules is None:
        return rules
    if not isinstance(rules, list):
        raise RuleSpecError(f"'{label}' must be a list of rule objects, got {type(rules).__name__}")

    known = ", ".join(sorted(specs))
    problems: List[str] = []

    for index, rule in enumerate(rules):
        where = f"{label}[{index}]"
        if not isinstance(rule, dict):
            problems.append(f"{where}: must be an object, got {type(rule).__name__}")
            continue

        raw_type = rule.get("type")
        if raw_type is None or (isinstance(raw_type, str) and not raw_type.strip()):
            problems.append(f"{where}: 'type' is required. Supported types: {known}")
            continue
        if not isinstance(raw_type, str):
            problems.append(f"{where}: 'type' must be a string, got {type(raw_type).__name__}")
            continue

        rule_type = raw_type.strip()
        spec = specs.get(rule_type)
        if spec is None:
            problems.append(
                f"{where}: unknown rule type '{rule_type}'. Supported types: {known}"
            )
            continue

        params = rule.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            problems.append(f"{where}: 'params' must be an object, got {type(params).__name__}")
            continue

        allowed = set(spec.required) | set(spec.optional)
        unknown = sorted(set(params) - allowed)
        if unknown:
            # The typo case: 'source_column' for 'source_columns' silently
            # disabled the rule and the run came back green.
            problems.append(
                f"{where} ({rule_type}): unknown param(s) {unknown}. "
                f"Allowed: {', '.join(sorted(allowed)) or 'none'}"
            )

        for key in spec.required:
            if key not in params:
                problems.append(f"{where} ({rule_type}): missing required param '{key}'")

        for group in spec.one_of:
            if not any(key in params for key in group):
                problems.append(
                    f"{where} ({rule_type}): needs one of {', '.join(group)}"
                )

        for key, value in params.items():
            param_spec = spec.params.get(key)
            if param_spec is not None and value is not None:
                _check_param(f"{where} ({rule_type})", key, value, param_spec, problems)

        # Both rule types take the same `when`, so both get the same check. A
        # typo'd operator on a mandatory rule narrows its trigger, which makes it
        # report FEWER violations -- the failure that looks like success.
        if rule_type == "composite_unique":
            for i, component in enumerate(params.get("columns") or []):
                if isinstance(component, str):
                    continue
                if not isinstance(component, dict) or not component.get("column"):
                    problems.append(f"{where} (composite_unique): columns[{i}] must be "
                                    "a column name or an object with a 'column'")
                    continue
                match = str(component.get("match", "exact")).strip().lower()
                if match not in ("exact", "fuzzy", "normalized", "semantic"):
                    # Silently treating an unknown mode as exact would narrow the
                    # rule and report fewer duplicates than production.
                    problems.append(
                        f"{where} (composite_unique): columns[{i}] match '{match}' "
                        "must be exact or fuzzy. For semantic matching use "
                        "semantic_unique.")

        if rule_type == "semantic_unique":
            try:
                SemanticSpec.parse(params)
            except SemanticRuleError as exc:
                problems.append(f"{where} (semantic_unique): {exc}")

        if rule_type in ("derive", "conditional_required") and isinstance(
            params.get("when"), (dict, list)
        ):
            _check_derive_condition(f"{where} ({rule_type})", params.get("when"), problems)

        _check_expression_params(f"{where} ({rule_type})", params, problems)

    if problems:
        raise RuleSpecError("; ".join(problems))
    return rules


def validate_validation_rules(rules: Any, *, label: str = "rules") -> Any:
    return validate_rules(rules, VALIDATION_RULE_SPECS, label=label)


def validate_schema_transform_rules(rules: Any, *, label: str = "rules") -> Any:
    return validate_rules(rules, SCHEMA_TRANSFORM_RULE_SPECS, label=label)


def validate_transformation_rules(rules: Any, *, label: str = "rules") -> Any:
    return validate_rules(rules, TRANSFORMATION_RULE_SPECS, label=label)


RULE_SPECS_BY_CATEGORY: Dict[str, Dict[str, RuleSpec]] = {
    "VALIDATION": VALIDATION_RULE_SPECS,
    "TRANSFORMATION": TRANSFORMATION_RULE_SPECS,
    "SCHEMA_TRANSFORMATION": SCHEMA_TRANSFORM_RULE_SPECS,
}
