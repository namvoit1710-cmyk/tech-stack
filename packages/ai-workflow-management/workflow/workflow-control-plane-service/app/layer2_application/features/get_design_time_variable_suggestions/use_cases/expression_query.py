"""Lenient parsing + matching for partial ``{{$source.path}}`` expressions.

Pure functions only — no IO, no app imports beyond the suggestion value
objects. The design-time autocomplete use case feeds the scopes built by the
shared providers through :func:`match` and gets back ranked
``(scope, path, match_kind)`` triples.

Match-kind taxonomy:

- ``all``       — emitted without a filter term (empty query, or a fully
                  typed source followed by ``.``)
- ``source``    — the source token itself is being suggested (``$va`` →
                  ``$variables``)
- ``prefix``    — the path matched the typed term as a case-insensitive
                  prefix
- ``substring`` — the path matched the term only as a substring
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Picker collapses singular ``$variable`` into the plural ``$variables`` scope:
# only ``$variables`` is surfaced, and a user typing ``$variable.`` is routed to
# it. (Runtime still resolves ``$variable`` distinctly via UserVariablesFrame.)
SOURCE_ALIASES: dict[str, str] = {"variable": "variables"}

# Trailing partial bracket the user is still typing: ``[``, ``[3``, ``[*``.
_TRAILING_PARTIAL_BRACKET = re.compile(r"\[[0-9*]*$")
# Complete numeric index → providers emit row paths with the ``[*]`` joint.
_NUMERIC_INDEX = re.compile(r"\[\d+\]")


@dataclass
class ParsedQuery:
    """Decomposition of the raw ``q`` text."""

    source_term: str = ""       # text before the first "."
    path_term: str = ""         # text after the first "."
    has_separator: bool = False  # a "." after the source token was typed
    explicit_source: bool = False  # raw q started with "{{" or "$"
    is_empty: bool = False


def parse_query(q: str) -> ParsedQuery:
    """Decompose a partial expression, tolerating every in-flight state.

    Accepts ``{{$nA.da``, ``$nA.da``, ``nA.da``, ``da``, ``$nA.``,
    ``$nA.orders[``, ``$nA.orders[0]``, trailing ``}}`` — anything a user
    might have typed mid-expression.
    """
    s = (q or "").strip()
    if s.startswith("{{"):
        s = s[2:].lstrip()
    s = s.rstrip("}").strip()
    explicit_source = s.startswith("$")
    if explicit_source:
        s = s[1:]
    if not s:
        return ParsedQuery(is_empty=True, explicit_source=explicit_source)
    if "." in s:
        source_term, path_term = s.split(".", 1)
        has_separator = True
    else:
        source_term, path_term, has_separator = s, "", False
    source_term = SOURCE_ALIASES.get(source_term.lower(), source_term)
    # Bracket tolerance on the path term: drop a partial trailing bracket,
    # canonicalize complete numeric indices to the ``[*]`` row joint.
    path_term = _TRAILING_PARTIAL_BRACKET.sub("", path_term)
    path_term = _NUMERIC_INDEX.sub("[*]", path_term)
    return ParsedQuery(
        source_term=source_term,
        path_term=path_term,
        has_separator=has_separator,
        explicit_source=explicit_source,
    )


def source_token(scope: Any) -> str:
    """The ``$``-token a scope answers to: node id for node scopes, the
    scope type otherwise (``run``, ``workflow``, ``variables``, ...)."""
    if getattr(scope, "scope_type", "") == "node":
        return getattr(scope, "scope_id", "") or ""
    return getattr(scope, "scope_type", "") or ""


@dataclass
class PathMatch:
    """One ranked emission: a path (or bare source) within a scope."""

    scope: Any
    path: Any  # VariablePath | None for match_kind == "source"
    match_kind: str  # all | source | prefix | substring


def _path_matches(scope: Any, term: str) -> list[PathMatch]:
    """Prefix matches first, substring fallbacks second, emission order kept."""
    lowered = term.lower()
    prefix: list[PathMatch] = []
    substring: list[PathMatch] = []
    for p in scope.paths:
        path_l = (p.path or "").lower()
        if path_l.startswith(lowered):
            prefix.append(PathMatch(scope=scope, path=p, match_kind="prefix"))
        elif lowered in path_l:
            substring.append(PathMatch(scope=scope, path=p, match_kind="substring"))
    return prefix + substring


def _all_paths(scope: Any) -> list[PathMatch]:
    return [PathMatch(scope=scope, path=p, match_kind="all") for p in scope.paths]


def match(parsed: ParsedQuery, scopes: list[Any]) -> list[PathMatch]:
    """Run the matching algorithm; returns emissions in ranked order.

    Callers truncate to their limit afterwards — counts before truncation
    are the per-scope ``match_count`` totals.
    """
    if parsed.is_empty:
        # "Get all from scope": every path of every scope, provider order.
        out: list[PathMatch] = []
        for scope in scopes:
            out.extend(_all_paths(scope))
        return out

    if not parsed.has_separator:
        term_l = parsed.source_term.lower()
        out = []
        for scope in scopes:
            if source_token(scope).lower().startswith(term_l):
                out.append(PathMatch(scope=scope, path=None, match_kind="source"))
        if parsed.explicit_source:
            # The user has fully typed one source (``$input``) and is about
            # to hit "." — surface its paths alongside the source chip.
            exact = [s for s in scopes if source_token(s).lower() == term_l]
            if len(exact) == 1:
                out.extend(_all_paths(exact[0]))
            return out
        # Bare term (no "$"): global search across paths of every scope.
        ranked: list[PathMatch] = []
        for scope in scopes:
            ranked.extend(_path_matches(scope, parsed.source_term))
        return out + _rank(ranked)

    # Source token complete — resolve the scope by exact token match.
    term_l = parsed.source_term.lower()
    scope = next((s for s in scopes if source_token(s).lower() == term_l), None)
    if scope is None:
        return []
    if not parsed.path_term:
        return _all_paths(scope)
    return _rank(_path_matches(scope, parsed.path_term))


def rank_key(m: PathMatch, original_index: int) -> tuple[int, int, int, int]:
    """Prefix beats substring; shallower then shorter paths first; stable."""
    path_str = m.path.path if m.path is not None else ""
    return (
        0 if m.match_kind == "prefix" else 1,
        path_str.count(".") + path_str.count("["),
        len(path_str),
        original_index,
    )


def _rank(matches: list[PathMatch]) -> list[PathMatch]:
    return [
        m for _, m in sorted(
            enumerate(matches), key=lambda im: rank_key(im[1], im[0]),
        )
    ]
