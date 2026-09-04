"""SA-1451 — pure field-selection helpers.

The single place that answers "given the fields present on a row and a
configured selection, which fields (in what order) should feed this artifact,
and what text do they compose into?". Shared by BOTH ingest paths (the SDK
background job runner and the governance upload use case) so the embedding-text
and graph-source-text composition can never drift between them.

Matching is **whitespace-trimmed and case-insensitive**: real CSV headers carry
stray spaces and inconsistent casing (``"Material Name "`` vs ``"material name"``),
and an operator's configured selection must still match them — otherwise a
selected column is silently dropped and the row embeds wrong. The *actual* row
header (original spacing/case) is always what gets returned and composed.

Pure functions only — no I/O, no framework, no app exceptions.
"""
from __future__ import annotations

from collections.abc import Sequence


def _norm(name: object) -> str:
    """Normalized key for matching: trimmed + case-folded."""
    return str(name).strip().casefold()


def resolve_included_fields(
    available_fields: Sequence[str],
    selected_fields: Sequence[str],
    *,
    fallback_to_all_when_empty: bool = False,
) -> list[str]:
    """Return the actual row-field names to include, in the order to appear.

    - ``selected_fields`` empty  -> every available field, in its given order
      (the backward-compatible "use all fields" default).
    - ``selected_fields`` given  -> the available fields whose *normalized* name
      matches a selected name, in the **selected** order (a row need not carry
      every configured column; absent ones are skipped).
    - ``fallback_to_all_when_empty`` -> if a non-empty selection matches NONE of
      this row's fields, fall back to every available field instead of returning
      an empty list. Embedding uses this (an empty embedding vector is strictly
      worse than embedding the whole row); graph extraction does not (if the row
      lacks the configured graph fields, it should contribute no graph entities).
    """
    available = [str(name) for name in available_fields]
    if not selected_fields:
        return available

    # normalized-header -> first actual header carrying that normalized form
    by_norm: dict[str, str] = {}
    for actual in available:
        by_norm.setdefault(_norm(actual), actual)

    included: list[str] = []
    seen: set[str] = set()
    for selected in selected_fields:
        actual = by_norm.get(_norm(selected))
        if actual is not None and actual not in seen:
            seen.add(actual)
            included.append(actual)

    if not included and fallback_to_all_when_empty:
        return available
    return included


def compose_field_text(
    fields: dict[str, str],
    included_fields: Sequence[str],
    *,
    separator: str,
) -> str:
    """Join ``name: value`` for each included field with ``separator``.

    Preserves each caller's existing formatting (the SDK path uses ``" | "``,
    the governance path uses ``"\\n"``) — SA-1451 only narrows *which* fields
    are composed, never how a given path formats them.
    """
    return separator.join(
        f"{name}: {fields[name]}"
        for name in included_fields
        if name in fields
    )


def unknown_fields(
    selected_fields: Sequence[str],
    available_fields: Sequence[str],
) -> list[str]:
    """Selected names that match no available field (normalized comparison).

    Returned sorted+deduped (by original text) so callers can raise a stable,
    clear validation error. Empty list means the selection is a valid subset.
    Matching is trimmed + case-insensitive, consistent with
    :func:`resolve_included_fields`.
    """
    available_norm = {_norm(name) for name in available_fields}
    missing = {
        str(name).strip()
        for name in selected_fields
        if _norm(name) not in available_norm
    }
    return sorted(missing)
