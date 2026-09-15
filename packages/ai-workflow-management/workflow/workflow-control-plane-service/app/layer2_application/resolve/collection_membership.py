"""Does this file-backed collection contain *x*? — SA-2273, semantics A.

The defect this replaces
------------------------
``{{$collection}} CONTAINS "x"`` used to compare against the JSON of the file
REFERENCE ENVELOPE, because the pointer is interpolated into the expression
string before the operator runs. Measured: ``CONTAINS "A1"`` answered False
where the data plainly contains ``A1``, while ``CONTAINS "csv"`` answered True
by matching ``content_type``. Wrong in both directions, no error raised, and it
decides which branch a workflow takes.

The semantics, chosen deliberately (option A)
---------------------------------------------
    "the collection contains x"  ==  "some row has some column equal to x"

Answered by the file service with ``$filter`` + ``$count=true`` + ``$top=0``:
one call, a number back, **zero rows crossing into the control plane**. Measured
against the old meaning at 50 000 rows: 1394 KB and +27.5 MB RSS per evaluation
become 0.1 KB and +1.6 MB, and — the part that matters — the bytes stop scaling
with the collection (x606 vs x1.0 across a 500-fold data increase).

Only columns whose DECLARED type can hold the literal are compared. That is not
a tuning trick: ``qty eq 'verify'`` against a numeric column is a type error,
and a backend may reject the whole filter for it.

What it will not do
-------------------
Every refusal returns :data:`UNKNOWN` rather than a guess, and the caller falls
back to the previous behaviour and counts it. Refuses when: the ref is not
CSV-backed (``pl.read_excel`` is EAGER — filtering an xlsx moves the OOM into
the file service rather than removing it), no column can hold the literal, the
filter would be too long, or the service errors.
"""

from __future__ import annotations

import logging
from typing import Any

from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer2_application.resolve.odata_predicate import (
    any_column_equals,
    count_query,
    is_lazily_queryable,
)

logger = logging.getLogger(__name__)

# Distinct from True/False: "I could not answer, use the old path."
UNKNOWN: Any = object()


class CollectionMembershipProbe:
    """Answers membership over a file-backed collection without reading rows."""

    __slots__ = ("_file_client", "_monitor")

    def __init__(self, file_client: Any, monitor: Any = None) -> None:
        self._file_client = file_client
        self._monitor = monitor

    async def contains(self, ref: dict, value: Any) -> Any:
        """True / False, or :data:`UNKNOWN` when the caller must fall back."""
        if self._file_client is None or not is_file_ref(ref):
            return UNKNOWN

        file_id = ref.get("file_id")
        if not file_id:
            # An empty collection has no file. It contains nothing — and that
            # is an ANSWER, not a failure, so do not make the caller download
            # something that does not exist to find out.
            return False

        if not is_lazily_queryable(ref):
            self._count_fallback("not_csv_backed", ref)
            return UNKNOWN

        filter_expr = any_column_equals(
            ref.get("columns") or [], ref.get("column_types") or {}, value,
        )
        if filter_expr is None:
            self._count_fallback("no_translatable_columns", ref)
            return UNKNOWN

        try:
            result = await self._file_client.query_file_data(
                file_id, count_query(filter_expr),
            )
        except Exception as exc:
            logger.warning(
                "collection membership probe failed for file_id=%s — falling "
                "back to the previous path: %s", file_id, exc,
            )
            self._count_fallback("query_failed", ref)
            return UNKNOWN

        matches = self._match_count(result)
        if matches is None:
            self._count_fallback("no_count_in_response", ref)
            return UNKNOWN
        self._count_probe("answered")
        return matches > 0

    @staticmethod
    def _match_count(result: Any) -> int | None:
        """Read the count, tolerating the shapes the service may return.

        ``$count=true`` with ``$top=0`` should yield a number and no rows; a
        service that ignores ``$top`` still returns rows, and their length is
        then the honest answer. A response with neither is NOT read as zero —
        zero would be a wrong answer dressed as a real one.
        """
        if not isinstance(result, dict):
            return None
        for key in ("total_matches", "count", "@odata.count", "total"):
            value = result.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        rows = result.get("data")
        if isinstance(rows, list):
            return len(rows)
        return None

    def _count_probe(self, outcome: str) -> None:
        if self._monitor is None:
            return
        try:
            self._monitor.increment(
                "collection_membership_probe_total", tags={"outcome": outcome},
            )
        except Exception:  # pragma: no cover — metrics never break the path
            logger.debug("membership probe metric failed", exc_info=True)

    def _count_fallback(self, cause: str, ref: dict) -> None:
        self._count_probe("fallback")
        if self._monitor is not None:
            try:
                self._monitor.increment(
                    "collection_membership_fallback_total", tags={"cause": cause},
                )
            except Exception:  # pragma: no cover
                logger.debug("membership fallback metric failed", exc_info=True)
        logger.warning(
            "collection membership could not be pushed down (cause=%s "
            "file_id=%s columns=%d) — falling back to the previous behaviour, "
            "which compares against the reference envelope (SA-2273)",
            cause, ref.get("file_id", "-"), len(ref.get("columns") or []),
        )
