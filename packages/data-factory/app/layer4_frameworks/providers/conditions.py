"""One condition vocabulary, shared by every rule that has a ``when``.

There were two. ``derive`` spoke fourteen upper-case operators and accepted
``{match: ALL|ANY, conditions: [...]}``; ``conditional_required`` spoke six
lower-case ones and accepted a single condition object. Same concept, same
payload position, two grammars - so a rule author who learned one had to
re-learn the other, and a mass-upload mandatory rule (WHEN a set of conditions
holds, THEN this field is required) could not be written at all.

This module is that grammar, once. It builds a polars expression and never
touches a frame, so the validation engine (lazy, no DataFrame in hand) and the
schema transformer (eager, frame available) can both use it.

Callers pass ``known_columns`` when they can. A condition naming a column the
data does not carry is dropped with a warning rather than raising: a derivation
whose trigger column is absent should leave the frame alone, not fail the run.
When ``known_columns`` is None the reference is left in the expression and
polars reports it, which is what the validation path wants - there, a rule
naming a column that is not in the file is an error the caller must see.
"""
from typing import Any, Callable, Dict, List, Optional, Sequence

import polars as pl

#: The canonical operators. ``derive``'s spelling wins because it is the larger
#: set - the six comparison operators have equivalents here, the set-membership
#: and emptiness ones do not.
OPERATORS = (
    "EQUAL", "NOT_EQUAL", "IN", "NOT_IN", "CONTAINS", "STARTS_WITH",
    "GT", "GTE", "LT", "LTE", "IS_NULL", "IS_EMPTY", "IS_NOT_NULL", "IS_NOT_EMPTY",
)

#: ``conditional_required``'s original lower-case spelling, kept working.
#:
#: ⚠️ ``lt``/``le``/``gt``/``ge`` used to compare as TEXT, which made
#: ``'9' < '10'`` false. They now compare numerically, like ``derive``'s
#: ``LT``/``LTE``/``GT``/``GTE``. That is a behaviour change and a deliberate
#: one: an ordering comparison on master data means the number. ``eq``/``ne``
#: are unaffected - they were text and stay text, so a leading zero still
#: survives.
ALIASES = {
    "eq": "EQUAL", "ne": "NOT_EQUAL",
    "lt": "LT", "le": "LTE", "gt": "GT", "ge": "GTE",
}


def canonical_operator(operator: Any) -> Optional[str]:
    """The canonical name for ``operator``, or None if it is not one of ours."""
    if operator is None:
        return "EQUAL"
    text = str(operator).strip()
    if not text:
        return "EQUAL"
    aliased = ALIASES.get(text.lower())
    if aliased:
        return aliased
    upper = text.upper()
    return upper if upper in OPERATORS else None


def single_condition(
    condition: Dict[str, Any],
    known_columns: Optional[Sequence[str]] = None,
    warn: Optional[Callable[[str], None]] = None,
) -> Optional[pl.Expr]:
    """One ``{column, operator, value}`` as a boolean expression."""
    def note(message: str) -> None:
        if warn is not None:
            warn(message)

    if not isinstance(condition, dict):
        note("condition must be an object. Ignoring condition.")
        return None

    column = str(condition.get("column") or condition.get("field") or "").strip()
    if not column:
        note("condition names no column. Ignoring condition.")
        return None
    if known_columns is not None and column not in known_columns:
        note(f"condition column '{column}' is not on the frame. Ignoring condition.")
        return None

    operator = canonical_operator(condition.get("operator"))
    if operator is None:
        note(
            f"unsupported operator '{condition.get('operator')}' on '{column}'. "
            f"Supported: {', '.join(OPERATORS)}. Ignoring condition."
        )
        return None

    value = condition.get("value")

    is_empty = pl.col(column).is_null() | (
        pl.col(column).cast(pl.Utf8, strict=False).str.strip_chars() == ""
    )
    if operator in ("IS_NULL", "IS_EMPTY"):
        return is_empty
    if operator in ("IS_NOT_NULL", "IS_NOT_EMPTY"):
        return ~is_empty

    if operator in ("GT", "GTE", "LT", "LTE"):
        # The only operators where text ordering would lie: '9' sorts after '10'.
        left = pl.col(column).cast(pl.Float64, strict=False)
        try:
            right = float(value)
        except (TypeError, ValueError):
            note(
                f"condition on '{column}' uses {operator} with non-numeric value "
                f"{value!r}. Ignoring condition."
            )
            return None
        return {"GT": left > right, "GTE": left >= right,
                "LT": left < right, "LTE": left <= right}[operator]

    # Everything else compares as text: configured values arrive as strings and
    # leading zeros ('01', '0001') are significant in master data.
    text = pl.col(column).cast(pl.Utf8, strict=False).str.strip_chars()
    if operator in ("IN", "NOT_IN"):
        members = text.is_in([str(item) for item in (value or [])])
        return members if operator == "IN" else ~members
    if operator == "CONTAINS":
        return text.str.contains(str(value), literal=True)
    if operator == "STARTS_WITH":
        return text.str.starts_with(str(value))
    if operator == "NOT_EQUAL":
        return text != str(value)
    return text == str(value)


def build_condition(
    when: Any,
    known_columns: Optional[Sequence[str]] = None,
    warn: Optional[Callable[[str], None]] = None,
) -> Optional[pl.Expr]:
    """``when`` is one condition, or ``{match: ALL|ANY, conditions: [...]}``.

    ``ALL`` is the default, and it is the one a mandatory rule wants: SMDG's
    ``whenLogicalExpression`` over its SOURCE conditions is an AND in every
    template read so far.
    """
    if not isinstance(when, dict) or not when:
        return None

    raw = when.get("conditions")
    conditions: List[Any] = [when] if raw is None else (raw if isinstance(raw, list) else [])

    expressions = [
        expression
        for expression in (
            single_condition(condition, known_columns, warn) for condition in conditions
        )
        if expression is not None
    ]
    if not expressions:
        return None

    use_any = str(when.get("match") or "ALL").strip().upper() == "ANY"
    combined = expressions[0]
    for expression in expressions[1:]:
        combined = combined | expression if use_any else combined & expression
    return combined
