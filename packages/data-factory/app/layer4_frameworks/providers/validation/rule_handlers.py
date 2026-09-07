import polars as pl
import logging
import re
from typing import Dict, Any, List

from app.layer4_frameworks.providers.conditions import build_condition
from app.layer4_frameworks.providers.expressions.safe_expression import (
    RuleExpressionError,
    evaluate_expression,
)

logger = logging.getLogger(__name__)


def field_path(rule: Any, field: str) -> str:
    """The dotted ``SECTION.field`` a rule is configured against.

    The template writes its rules against a field path and the frontend renders
    errors by that path, but the workbook only knows column labels. The caller
    resolves one to the other and hands the map down on the rule; when it did
    not, the label is the most truthful thing we have and is used unchanged.
    """
    paths = getattr(rule, "field_paths", None) or {}
    return str(paths.get(field) or field)


class RequiredRuleHandler:
    def parse_rule(self, rule: Any, alias: str) -> List[pl.Expr]:
        expressions = []
        for col in rule.params.get('columns', []):
            cond = pl.col(col).is_null() | (pl.col(col).cast(pl.Utf8).str.strip_chars() == "")
            struct = pl.struct([pl.lit(rule.rule_name).alias("rule"), pl.lit(col).alias("field"), pl.lit(field_path(rule, col)).alias("path"), pl.lit(rule.error_message).alias("message"), pl.col(col).cast(pl.Utf8).alias("value")])
            expressions.append(pl.when(cond).then(struct).otherwise(None).alias(f"{alias}_{col}"))
        return expressions


class ExpressionRuleHandler:
    def parse_rule(self, rule: Any, alias: str) -> List[pl.Expr]:
        expr_str = rule.params.get('expression')
        if not expr_str:
            return []
        try:
            cond = evaluate_expression(
                expr_str, columns=rule.params.get('columns') or []
            )
            cols = rule.params.get('columns', []) or []
            struct = pl.struct([pl.lit(rule.rule_name).alias("rule"), pl.lit(str(cols)).alias("field"), pl.lit(", ".join(field_path(rule, c) for c in cols)).alias("path"), pl.lit(rule.error_message).alias("message"), pl.lit("Expr").alias("value")])
            return [pl.when(cond.not_()).then(struct).otherwise(None).alias(alias)]
        except RuleExpressionError as exc:
            # Not a warning-and-skip. A rule that could not compile used to be
            # dropped here, which is how an invalid rule set came back green.
            raise RuleExpressionError(
                f"validation rule '{rule.rule_name}': {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Declarative rule types.
#
# Everything below can be written as an `expression`, and for a long time was.
# The difference is not power, it is who can read and edit the rule: LENGTH_40
# as `{"type": "length", "params": {"columns": ["baseUnit"], "max": 40}}` is a
# number in a field that a form can render and the boundary can range-check.
# The same rule as `pl.col('baseUnit').str.len_chars() <= 40` is a program, and
# changing 40 to 50 means editing code.
#
# `expression` stays for everything these do not cover. This is the common half
# lifted out of it, not a replacement.
#
# Null handling is uniform and deliberate: a null is *not* a violation of any
# rule here. Absence is what `required` is for, and a row that is missing a
# value should fail one rule rather than every rule that mentions the column.
# ---------------------------------------------------------------------------


def _violation(rule: Any, field: str, condition: pl.Expr, value: pl.Expr) -> pl.Expr:
    """The struct shape every handler reports, wrapped in its trigger."""
    struct = pl.struct([
        pl.lit(rule.rule_name).alias("rule"),
        pl.lit(field).alias("field"),
        pl.lit(field_path(rule, field)).alias("path"),
        pl.lit(rule.error_message).alias("message"),
        value.cast(pl.Utf8).alias("value"),
    ])
    return pl.when(condition).then(struct).otherwise(None)


def _present(column: str) -> pl.Expr:
    return pl.col(column).is_not_null()


def _require(rule: Any, *keys: str) -> None:
    missing = [k for k in keys if rule.params.get(k) is None]
    if missing:
        raise RuleParamError(
            f"rule '{rule.rule_name}' ({rule.type}): missing {', '.join(missing)}"
        )


class RuleParamError(ValueError):
    """A declarative rule whose params the handler cannot act on."""


class LengthRuleHandler:
    """Character length, on the text form of the value."""

    def parse_rule(self, rule: Any, alias: str) -> List[pl.Expr]:
        params = rule.params
        minimum, maximum = params.get("min"), params.get("max")
        if minimum is None and maximum is None:
            raise RuleParamError(
                f"rule '{rule.rule_name}' (length): needs min, max, or both"
            )
        out = []
        for column in params.get("columns", []):
            length = pl.col(column).cast(pl.Utf8).str.len_chars()
            bad = pl.lit(False)
            if minimum is not None:
                bad = bad | (length < minimum)
            if maximum is not None:
                bad = bad | (length > maximum)
            out.append(
                _violation(rule, column, _present(column) & bad, pl.col(column))
                .alias(f"{alias}_{column}")
            )
        return out


class PatternRuleHandler:
    """Regex. `full_match` anchors it, which is usually what people mean."""

    def parse_rule(self, rule: Any, alias: str) -> List[pl.Expr]:
        _require(rule, "pattern")
        pattern = rule.params["pattern"]
        if rule.params.get("full_match"):
            pattern = f"^(?:{pattern})$"
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RuleParamError(
                f"rule '{rule.rule_name}' (pattern): not a valid regex — {exc}"
            ) from exc

        out = []
        for column in rule.params.get("columns", []):
            matches = pl.col(column).cast(pl.Utf8).str.contains(pattern)
            out.append(
                _violation(rule, column, _present(column) & ~matches.fill_null(False),
                           pl.col(column))
                .alias(f"{alias}_{column}")
            )
        return out


class RangeRuleHandler:
    """Numeric bounds, inclusive unless told otherwise.

    A value that is not a number at all is a violation: silently skipping it
    would report a text 'N/A' in a numeric column as being in range.
    """

    def parse_rule(self, rule: Any, alias: str) -> List[pl.Expr]:
        params = rule.params
        minimum, maximum = params.get("min"), params.get("max")
        if minimum is None and maximum is None:
            raise RuleParamError(
                f"rule '{rule.rule_name}' (range): needs min, max, or both"
            )
        exclusive = bool(params.get("exclusive"))

        out = []
        for column in params.get("columns", []):
            number = pl.col(column).cast(pl.Float64, strict=False)
            unparseable = _present(column) & number.is_null()
            bad = pl.lit(False)
            if minimum is not None:
                bad = bad | (number <= minimum if exclusive else number < minimum)
            if maximum is not None:
                bad = bad | (number >= maximum if exclusive else number > maximum)
            condition = unparseable | (_present(column) & bad.fill_null(False))
            out.append(
                _violation(rule, column, condition, pl.col(column))
                .alias(f"{alias}_{column}")
            )
        return out


class DateRangeRuleHandler:
    """Calendar bounds. Text dates are parsed with `format` before comparing."""

    def parse_rule(self, rule: Any, alias: str) -> List[pl.Expr]:
        params = rule.params
        after, before = params.get("after"), params.get("before")
        if after is None and before is None:
            raise RuleParamError(
                f"rule '{rule.rule_name}' (date_range): needs after, before, or both"
            )
        fmt = params.get("format", "%Y-%m-%d")

        out = []
        for column in params.get("columns", []):
            as_date = (
                pl.col(column).cast(pl.Utf8).str.to_date(fmt, strict=False)
            )
            unparseable = _present(column) & as_date.is_null()
            bad = pl.lit(False)
            if after is not None:
                bad = bad | (as_date < pl.lit(after).str.to_date(fmt, strict=False))
            if before is not None:
                bad = bad | (as_date > pl.lit(before).str.to_date(fmt, strict=False))
            condition = unparseable | (_present(column) & bad.fill_null(False))
            out.append(
                _violation(rule, column, condition, pl.col(column))
                .alias(f"{alias}_{column}")
            )
        return out


class AllowedValuesRuleHandler:
    """A closed set, held in the rule rather than in a reference file.

    `reference_lookup` is the right tool when the set lives in data and moves.
    This is for the short fixed lists — a status, a unit, a flag.
    """

    def parse_rule(self, rule: Any, alias: str) -> List[pl.Expr]:
        _require(rule, "values")
        values = rule.params["values"]
        if not isinstance(values, (list, tuple)) or not values:
            raise RuleParamError(
                f"rule '{rule.rule_name}' (allowed_values): 'values' must be a "
                "non-empty list"
            )
        allowed = [str(v) for v in values]
        case_sensitive = rule.params.get("case_sensitive", True)

        out = []
        for column in rule.params.get("columns", []):
            value = pl.col(column).cast(pl.Utf8)
            candidates = allowed
            if not case_sensitive:
                value = value.str.to_lowercase()
                candidates = [v.lower() for v in allowed]
            out.append(
                _violation(rule, column,
                           _present(column) & ~value.is_in(candidates),
                           pl.col(column))
                .alias(f"{alias}_{column}")
            )
        return out


_COMPARISONS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
}


class CompareFieldsRuleHandler:
    """One column against another — 'valid_to must not precede valid_from'."""

    def parse_rule(self, rule: Any, alias: str) -> List[pl.Expr]:
        _require(rule, "left", "right", "operator")
        params = rule.params
        operator = str(params["operator"]).strip().lower()
        compare = _COMPARISONS.get(operator)
        if compare is None:
            raise RuleParamError(
                f"rule '{rule.rule_name}' (compare_fields): unknown operator "
                f"'{operator}'. Use one of {', '.join(sorted(_COMPARISONS))}"
            )
        left, right = params["left"], params["right"]
        numeric = bool(params.get("numeric"))

        a, b = pl.col(left), pl.col(right)
        if numeric:
            a = a.cast(pl.Float64, strict=False)
            b = b.cast(pl.Float64, strict=False)
        else:
            a, b = a.cast(pl.Utf8), b.cast(pl.Utf8)

        # Only judge rows where both sides are present; a missing operand is a
        # `required` problem, not a comparison failure.
        both = pl.col(left).is_not_null() & pl.col(right).is_not_null()
        holds = compare(a, b).fill_null(False)
        return [
            _violation(rule, left, both & ~holds, pl.col(left)).alias(alias)
        ]


class ConditionalRequiredRuleHandler:
    """'If category is FOOD, temperature must be filled in.'

    The single most common shape in a mass-upload template, and the one that
    previously forced an author into `expression` for something a form could
    have collected.

    `when` takes one condition, or several::

        {"when": {"match": "ALL", "conditions": [
            {"column": "Product.materialType",  "operator": "EQUAL",     "value": "VERP"},
            {"column": "Product.baseUnit",      "operator": "IS_NOT_EMPTY"}]},
         "then": ["Product.grossWeight", "Product.weightUnit"]}

    That is SMDG's `MandatoryRule` shape - a `whenLogicalExpression` over a set
    of SOURCE conditions - which a single `{column, operator, value}` could not
    express, so those rules had to be written as `expression` or not at all.
    The grammar is shared with `derive`; see `providers.conditions`.
    """

    def parse_rule(self, rule: Any, alias: str) -> List[pl.Expr]:
        _require(rule, "when", "then")
        when = rule.params["when"]
        if not isinstance(when, dict):
            raise RuleParamError(
                f"rule '{rule.rule_name}' (conditional_required): 'when' must be "
                "an object"
            )
        columns = rule.params["then"]
        if isinstance(columns, str):
            columns = [columns]
        if not columns:
            raise RuleParamError(
                f"rule '{rule.rule_name}' (conditional_required): 'then' names "
                "no columns"
            )

        # No `known_columns`: on the validation path a rule naming a column the
        # file does not carry is the caller's error and must surface, not be
        # quietly dropped the way a derivation's trigger is.
        problems: List[str] = []
        trigger = build_condition(when, warn=problems.append)
        if trigger is None:
            raise RuleParamError(
                f"rule '{rule.rule_name}' (conditional_required): 'when' has no "
                f"usable condition"
                + (f" - {'; '.join(problems)}" if problems else "")
            )
        if problems:
            # Some conditions parsed and some did not. Narrowing a mandatory
            # rule's trigger makes it fire less often, i.e. it under-reports.
            raise RuleParamError(
                f"rule '{rule.rule_name}' (conditional_required): "
                + "; ".join(problems)
            )
        trigger = trigger.fill_null(False)

        out = []
        for column in columns:
            blank = (
                pl.col(column).is_null()
                | (pl.col(column).cast(pl.Utf8).str.strip_chars() == "")
            )
            out.append(
                _violation(rule, column, trigger & blank, pl.col(column))
                .alias(f"{alias}_{column}")
            )
        return out


# ---------------------------------------------------------------------------
# Near-duplicate detection.
#
# `unique` answers "is this value repeated". Master data asks a softer question:
# ' ACME Ltd.' and 'acme ltd' are the same vendor typed twice, and an exact check
# reports neither. The obvious implementation - compare every row against every
# other - is O(n^2), which at 2M rows is 2*10^12 comparisons and never finishes.
#
# So the comparison is never made. Each method derives a BLOCKING KEY that
# collapses the variations it is meant to ignore, and the ordinary duplicate check
# then runs on that key. The cost is the same as `unique`; only the key differs.
#
# Every key here is also expressible in SQL (UPPER + REPLACE_REGEXPR, or a
# digits-only strip), which is what lets a HANA-sourced run group on it in the
# database rather than pull the column out to decide.
# ---------------------------------------------------------------------------

FUZZY_KEYS = {
    # ignore case, spacing and punctuation: 'ACME-Ltd.' == 'acme ltd'
    "normalized": lambda c: (pl.col(c).cast(pl.Utf8).str.to_uppercase()
                             .str.replace_all(r"[^A-Z0-9]", "")),
    # digits only, for EAN / phone / tax numbers that carry formatting
    "digits": lambda c: pl.col(c).cast(pl.Utf8).str.replace_all(r"[^0-9]", ""),
    # the gentlest: case and surrounding whitespace only
    "trimmed": lambda c: (pl.col(c).cast(pl.Utf8).str.strip_chars()
                          .str.to_uppercase()),
}


class FuzzyUniqueRuleHandler:
    """`{"type": "fuzzy_unique", "params": {"columns": [...], "method": "normalized"}}`

    A blank cell is not a near-duplicate of another blank cell: blanks are
    `required`'s business, and reporting them here would flag every incomplete row
    a second time under a rule that is not about completeness.
    """

    def parse_rule(self, rule: Any, alias: str) -> List[pl.Expr]:
        method = rule.params.get("method", "normalized")
        if method not in FUZZY_KEYS:
            raise RuleParamError(
                f"rule '{rule.rule_name}' (fuzzy_unique): method '{method}' is not "
                f"one of {', '.join(sorted(FUZZY_KEYS))}"
            )
        _require(rule, "columns")
        out = []
        for column in rule.params.get("columns", []):
            key = FUZZY_KEYS[method](column)
            meaningful = _present(column) & (key.str.len_chars() > 0)
            out.append(
                _violation(rule, column, meaningful & key.is_duplicated(),
                           pl.col(column))
                .alias(f"{alias}_{column}")
            )
        return out


class RuleFactory:
    def __init__(self):
        self.handlers = {
            'required': RequiredRuleHandler(),
            'expression': ExpressionRuleHandler(),
            'length': LengthRuleHandler(),
            'pattern': PatternRuleHandler(),
            'range': RangeRuleHandler(),
            'date_range': DateRangeRuleHandler(),
            'allowed_values': AllowedValuesRuleHandler(),
            'compare_fields': CompareFieldsRuleHandler(),
            'conditional_required': ConditionalRequiredRuleHandler(),
            'fuzzy_unique': FuzzyUniqueRuleHandler(),
        }

    def get_handler(self, rule_type: str): return self.handlers.get(rule_type)
