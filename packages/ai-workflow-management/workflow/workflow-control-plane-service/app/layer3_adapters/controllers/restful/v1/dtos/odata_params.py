"""Shared OData-style query parameter parser for list endpoints.

Supports: $skip, $top, $filter, $orderby, $count.
Filter syntax: ``field <op> 'value'`` (op ∈ eq, ne, gt, ge, lt, le) and
``contains(field, 'value')`` joined by ``and``.

Usage::

    @router.get("")
    async def list_items(request: Request):
        params = parse_odata_params(request)
        filters = parse_odata_filter(params.filter)
        # filters.eq  -> {"status": "ACTIVE"}
        # filters.contains -> {"name": "approval"}
        ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from starlette.requests import Request


@dataclass
class ODataParams:
    skip: int = 0
    top: int = 20
    filter: str = ""
    orderby: str = ""
    count: bool = False
    id: str | None = None
    version: str | None = None
    status: str | None = None


@dataclass
class ODataFilterResult:
    eq: dict[str, str] = field(default_factory=dict)
    ne: dict[str, str] = field(default_factory=dict)
    gt: dict[str, str] = field(default_factory=dict)
    ge: dict[str, str] = field(default_factory=dict)
    lt: dict[str, str] = field(default_factory=dict)
    le: dict[str, str] = field(default_factory=dict)
    contains: dict[str, str] = field(default_factory=dict)


_CONTAINS_RE = re.compile(r"contains\(\s*(\w+)\s*,\s*'([^']*)'\s*\)")
_COMPARISON_OPS = ("eq", "ne", "gt", "ge", "lt", "le")


def _coerce_eq_value(raw: str) -> Any:
    """Coerce a right-hand side OData literal to its Python type.

    Quoted strings ``'foo'`` / ``"foo"`` stay strings (quotes stripped).
    Unquoted ``true`` / ``false`` become booleans. Unquoted integers become ints.
    Anything else is returned as the original string.
    """
    token = raw.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    lowered = token.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if token.lstrip("-").isdigit():
        return int(token)
    return token


def parse_odata_params(request: Request) -> ODataParams:
    """Extract OData query parameters from a request.

    Also accepts ``offset``/``limit`` as aliases for backward compatibility.
    """
    params = request.query_params
    skip = int(params.get("$skip", "") or params.get("offset", "") or "0")
    top = int(params.get("$top", "") or params.get("limit", "") or "20")
    top = min(top, 100)  # cap at 100 per page
    return ODataParams(
        skip=max(skip, 0),
        top=max(top, 1),
        filter=params.get("$filter", ""),
        orderby=params.get("$orderby", ""),
        count=params.get("$count", "").lower() == "true",
        id=params.get("$id", None),
        version=params.get("$version", None),
        status=params.get("$status", None),
    )


def parse_odata_filter(filter_str: str) -> ODataFilterResult:
    """Parse OData ``$filter`` expression.

    Supports comparison operators ``eq``, ``ne``, ``gt``, ``ge``, ``lt``, ``le``
    and ``contains(field, 'value')`` joined by ``and``.

    Example::

        parse_odata_filter("status eq 'ACTIVE' and contains(name, 'approval')")
        # → ODataFilterResult(eq={"status": "ACTIVE"}, contains={"name": "approval"})
    """
    result = ODataFilterResult()
    if not filter_str:
        return result

    # Extract contains() calls first
    for match in _CONTAINS_RE.finditer(filter_str):
        result.contains[match.group(1)] = match.group(2)

    # Remove contains() calls, then parse remaining comparison expressions
    remaining = _CONTAINS_RE.sub("", filter_str)
    parts = remaining.split(" and ")
    for part in parts:
        tokens = part.strip().split(" ", 2)
        if len(tokens) < 3:
            continue
        op = tokens[1].lower()
        if op not in _COMPARISON_OPS:
            continue
        key = tokens[0].strip()
        value = tokens[2].strip().strip("'\"")
        getattr(result, op)[key] = value

    return result


def odata_page_response(items: list[Any], total: int, params: ODataParams) -> dict:
    """Build a standardized paged response with OData metadata."""
    return {
        "items": items,
        "total": total,
        "$skip": params.skip,
        "$top": params.top,
    }
