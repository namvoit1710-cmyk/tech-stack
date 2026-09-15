"""Translate CONDITION rules into OData ``$filter`` — SA-2263 (R2).

Why push the predicate down
---------------------------
The file service is genuinely lazy for CSV: ``pl.scan_csv`` LazyFrame, slice-only
collect, count via ``select(pl.len())``, streamed to local disk, with memory
admission control sized from the cgroup limit. Asking it "how many rows match X"
returns **a number**, with zero rows crossing into the control plane — the
cheapest possible answer to a condition over a large file.

What this module is, and is not
-------------------------------
It is the translator and its guards, with an operator table that mirrors
``ConditionEvaluator``'s (``variable_resolver.py`` ``_CONDITION_OP_PATTERN``)
one-for-one. It deliberately does NOT decide what a predicate over a whole
collection *means*; see the note on semantics at the bottom of this docstring.

Two guards that are the whole point
-----------------------------------
* **Untranslatable → fall back, never guess.** A predicate this module cannot
  express returns ``None``. The caller falls back to the row-range path — never
  to a full materialize — and counts the fallback, so a translator that quietly
  stops translating is visible instead of merely slow.
* **EXCEL GUARD.** ``pl.read_excel`` is EAGER: an Excel-backed file is loaded
  whole, so pushing a predicate to it relocates the OOM rather than removing it.
  Only ``.csv`` may take this path. An Excel-backed ref is refused explicitly,
  never silently loaded.

A semantics note, deliberately left open
----------------------------------------
Translating a predicate over a WHOLE collection (``{{$node}} CONTAINS "x"``)
needs a product decision this module must not make unilaterally: today the
inline path compares against the JSON of the data and the file-backed path
compares against the JSON of the *reference envelope*, so the two disagree in
both directions. That divergence is a correctness defect in its own right and
is reported separately. This module gives that fix its vocabulary; it does not
pick the meaning.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Mirrors ``_CONDITION_OP_PATTERN`` in variable_resolver.py. Kept as an explicit
# table rather than derived, so adding an operator there without considering
# pushdown here fails a test instead of silently degrading to a full read.
COMPARISON_OPERATORS = frozenset({"==", "!=", ">", ">=", "<", "<=", "IN", "CONTAINS"})

_ODATA_COMPARISON = {
    "==": "eq",
    "!=": "ne",
    ">": "gt",
    ">=": "ge",
    "<": "lt",
    "<=": "le",
}

# File extensions / content types the file service reads LAZILY. Anything else
# is eager, and pushing a predicate at it moves the OOM instead of removing it.
_LAZY_CONTENT_TYPES = frozenset({"text/csv", "application/csv"})
_LAZY_SUFFIXES = (".csv",)


class UnsupportedPredicate(Exception):
    """Raised only by :func:`require_filter`; the normal API returns ``None``."""


def is_lazily_queryable(file_ref: dict[str, Any]) -> bool:
    """True when the file service can filter this ref WITHOUT loading it whole.

    The EXCEL GUARD. ``pl.read_excel`` is eager, so an ``.xlsx`` ref answers a
    ``$filter`` only by materialising the sheet inside the file service — the
    same OOM, one process to the left. A ref that does not clearly declare a
    lazy format is refused: unknown must not read as safe.
    """
    if not isinstance(file_ref, dict):
        return False
    content_type = str(file_ref.get("content_type") or "").split(";")[0].strip().lower()
    if content_type:
        return content_type in _LAZY_CONTENT_TYPES
    name = str(file_ref.get("file_name") or file_ref.get("name") or "").lower()
    return name.endswith(_LAZY_SUFFIXES)


def _literal(value: Any) -> str | None:
    """Render *value* as an OData literal, or ``None`` if it cannot be."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # Single quotes are the OData string delimiter; the escape is doubling.
        return "'" + value.replace("'", "''") + "'"
    return None


def translate(key: str, operation: str, value: Any) -> str | None:
    """Return an OData ``$filter`` for one rule, or ``None`` if untranslatable.

    ``None`` is a first-class answer, not a failure: the caller falls back to
    the row-range read and counts it. Returning a WRONG filter would be far
    worse than returning nothing — it would silently change which rows match.
    """
    if not key or not isinstance(key, str):
        return None
    field = key.strip()
    if not field or not _is_safe_field(field):
        return None

    # ABSENT means ``==`` (mirroring ``rule.get("operations", "==")`` in
    # condition_handler). An EMPTY STRING does not: the evaluator's regex fails
    # to match a blank operator and falls through to a truthiness test, so
    # defaulting it here would invent a comparison the evaluator never makes.
    if operation is None:
        op = "=="
    else:
        op = str(operation).strip().upper()
        if op == "=":
            op = "=="
    if op not in COMPARISON_OPERATORS:
        return None

    if op in _ODATA_COMPARISON:
        literal = _literal(value)
        if literal is None:
            return None
        return f"{field} {_ODATA_COMPARISON[op]} {literal}"

    if op == "CONTAINS":
        # substringof(needle, haystack): the FIELD is the haystack.
        literal = _literal(value if isinstance(value, str) else str(value))
        return f"contains({field}, {literal})" if literal else None

    if op == "IN":
        members = _in_members(value)
        if not members:
            return None
        rendered = [_literal(m) for m in members]
        if any(r is None for r in rendered):
            return None
        # No outer parentheses — see the note in ``any_column_equals``. This
        # was wrapped when first written and would have failed against the
        # real parser; nothing consumed it yet, so nothing regressed.
        return " or ".join(f"{field} eq {r}" for r in rendered)

    return None


def translate_all(rules: list[dict[str, Any]]) -> tuple[str | None, list[dict]]:
    """Translate a rule list ANDed together.

    Returns ``(filter_or_None, untranslated_rules)``. A partial translation is
    NOT returned as a filter: filtering on a subset of an ANDed predicate
    returns MORE rows than the predicate allows, and a caller that trusted it
    would act on rows the condition excluded. All or nothing.
    """
    if not rules:
        return None, []
    parts: list[str] = []
    untranslated: list[dict] = []
    for rule in rules:
        if not isinstance(rule, dict):
            untranslated.append({"rule": rule, "reason": "not-a-mapping"})
            continue
        rendered = translate(
            rule.get("key", ""), rule.get("operations", "=="), rule.get("value"),
        )
        if rendered is None:
            untranslated.append({"rule": rule, "reason": "untranslatable"})
        else:
            parts.append(rendered)
    if untranslated or not parts:
        return None, untranslated
    return " and ".join(parts), []


# Column-type vocabulary produced by CsvConverter (`_infer_type`).
_TYPE_STRING = "string"
_TYPE_NUMBER = "number"
_TYPE_BOOLEAN = "boolean"
_TYPE_JSON = "json"

# A filter is a query-string parameter. Many gateways cap the request line at
# ~8KB, and the any-column form grows with COLUMN count, so it is capped by
# both column count and encoded length. Exceeding either falls back rather
# than sending a request that may be truncated or rejected.
MAX_FILTER_COLUMNS = 40
MAX_FILTER_ENCODED_BYTES = 4000


def _literal_type(value: Any) -> str:
    if isinstance(value, bool):
        return _TYPE_BOOLEAN
    if isinstance(value, (int, float)):
        return _TYPE_NUMBER
    return _TYPE_STRING


def eligible_columns(
    columns: list, column_types: dict, value: Any,
) -> list[str]:
    """Columns whose DECLARED type can hold *value* — SA-2273 semantics A.

    Type-narrowing is not an optimisation bolted on afterwards; it is what
    makes the predicate correct. ``qty eq 'verify'`` against a numeric column
    is a type error, not a non-match, and a backend is entitled to reject the
    whole filter for it. Narrowing to the columns that can actually hold the
    literal removes that class of failure — and shortens the filter, which is
    the other constraint.

    A ``json`` column is included for a string literal: a JSON cell is stored
    as text, so a string CAN occur in it. Synthetic columns are excluded — they
    are storage bookkeeping, never data, and matching on them is exactly the
    envelope-vocabulary bug this replaces.
    """
    wanted = _literal_type(value)
    types = column_types or {}
    out = []
    for name in columns or []:
        if not isinstance(name, str) or name.startswith("__"):
            continue
        declared = str(types.get(name) or _TYPE_STRING).lower()
        if declared == wanted or (wanted == _TYPE_STRING and declared == _TYPE_JSON):
            out.append(name)
    return out


def any_column_equals(
    columns: list, column_types: dict, value: Any,
    max_columns: int = MAX_FILTER_COLUMNS,
) -> str | None:
    """``(c1 eq v or c2 eq v ...)`` over type-eligible columns, or ``None``.

    ``None`` means "ask someone else" — the caller falls back and counts it.
    Returned only when the filter is safe to send: at least one eligible
    column, not more than *max_columns*, and within the encoded-length budget.
    """
    eligible = eligible_columns(columns, column_types, value)
    if not eligible or len(eligible) > max_columns:
        return None
    literal = _literal(value)
    if literal is None:
        return None
    if not all(_is_safe_field(name) for name in eligible):
        return None
    # NO outer parentheses — this is load-bearing, not style. The file
    # service's parser splits on ' or ' with ``_split_by_operator``, which
    # respects parentheses: wrapped in one pair, the whole expression sits at
    # depth 1, nothing splits, and the entire string is handed to
    # ``_parse_predicate`` as a single predicate, which fails. Even if it did
    # split, ``_extract_field_name`` anchors on ``^<field>\s+`` and a leading
    # '(' does not match. Verified by reading
    # apps/backend/file/app/layer3_adapters/odata/odata_parser.py — the mock
    # client hides this because it ignores filters it cannot parse and returns
    # every row, which reads as "contains everything".
    rendered = " or ".join(f"{name} eq {literal}" for name in eligible)
    if len(quote(rendered)) > MAX_FILTER_ENCODED_BYTES:
        return None
    return rendered


def count_query(filter_expr: str) -> dict[str, str]:
    """The cheapest possible existence check: a count, and zero rows.

    ``$top=0`` matters as much as ``$count=true``: without it the service is
    entitled to return the matching rows alongside the number, which is the
    transfer this exists to avoid.
    """
    return {"$filter": filter_expr, "$count": "true", "$top": "0"}


def _is_safe_field(field: str) -> bool:
    """Reject anything that is not a plain column path.

    A field name is interpolated into the query string, so quotes, parentheses
    and spaces are refused rather than escaped — an unparsed filter is a wrong
    answer, and a maliciously shaped one is worse. Dots are allowed for nested
    columns; the file service flattens them.
    """
    if len(field) > 128:
        return False
    return all(ch.isalnum() or ch in "._" for ch in field)


def _in_members(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def require_filter(key: str, operation: str, value: Any) -> str:
    """``translate`` that raises instead of returning ``None`` — tests only."""
    rendered = translate(key, operation, value)
    if rendered is None:
        raise UnsupportedPredicate(f"{key!r} {operation!r} {value!r}")
    return rendered
