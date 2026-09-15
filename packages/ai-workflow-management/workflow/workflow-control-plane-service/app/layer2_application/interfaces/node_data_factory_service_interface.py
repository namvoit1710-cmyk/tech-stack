"""Port for delegating node-data artifact mutations to an external data-factory.

Higher-level than ``IDataFactoryClient`` (which is a thin HTTP wrapper for the
current row-transform service). This port hides the concrete factory backend
from the use case layer so alternate implementations — a different vendor, a
local Polars-based factory, a streaming pipeline — can slot in without
touching the PATCH /data use case.

Mapping contract:
- ``RowOperation`` is the neutral representation; adapters translate it into
  their backend's payload (for ``RowTransformFactoryService`` that is the
  ``POST /api/v1/transformation/rows`` schema described in row-transform.md).
- The data-factory mutates the file in place, creating a new *version* of
  the same ``file_id``. Adapters return the stable ``file_id`` plus the new
  ``version_id``; the caller keeps ``file_id`` as the artifact identity and
  records ``version_id`` on the working ``__file_ref`` tree. ``version_id``
  threaded back into a subsequent call pins that op's read to the revision
  the previous op produced (multi-op atomicity).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol


RowOpType = Literal["insert", "delete"]
RowIdentifierType = Literal["index", "condition", "values"]
InsertPosition = Literal["before", "after", "at_index"]


@dataclass
class RowIdentifier:
    """How to locate the row(s) an op targets. Exactly one of
    ``index`` / ``expression`` / ``match_values`` should be set, matching
    ``type``."""
    type: RowIdentifierType
    index: Optional[int] = None
    expression: Optional[str] = None
    match_values: Optional[dict[str, Any]] = None


@dataclass
class RowOperation:
    """A single insert or delete against one file."""
    operation: RowOpType
    row_identifier: RowIdentifier
    data: Optional[dict[str, Any]] = None       # insert only
    position: Optional[InsertPosition] = None    # insert only
    use_column_indices: bool = False


@dataclass
class RowTransformResult:
    """Unified response shape all adapters MUST produce.

    ``file_id`` is the stable artifact identity; ``version_id`` is the new
    revision the factory produced (empty when the backend doesn't version,
    e.g. the in-memory mock).
    """
    success: bool
    file_id: str = ""
    version_id: str = ""
    affected_rows: int = 0
    total_rows: int = 0
    message: str = ""
    error_code: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class INodeDataFactoryService(Protocol):
    """Abstract node-data factory. Future data-factory integrations
    (different vendors, local compute, streaming backends) should
    implement this Protocol and register under the same container key
    so the PATCH-via-factory use case can swap between them without
    code changes.
    """

    async def apply_row_operations(
        self,
        file_id: str,
        operations: list[RowOperation],
        file_format: str = "csv",
        version_id: Optional[str] = None,
    ) -> RowTransformResult:
        """Apply insert/delete operations against ``file_id`` and return the
        stable ``file_id`` plus the new ``version_id`` of the mutated
        artifact. ``version_id`` (input) pins the read to a specific prior
        revision; omit it to read the latest. On adapter-level failure,
        returns ``success=False`` with ``error_code``/``message`` populated —
        callers treat this as a terminal op error, NOT a raise.
        """
        ...

    async def add_columns(
        self,
        file_id: str,
        columns: list[str],
        *,
        default: Any = "",
        version_id: Optional[str] = None,
    ) -> RowTransformResult:
        """Add one or more columns to ``file_id`` (each backfilled with
        ``default`` on existing rows) and return the stable ``file_id`` plus the
        new ``version_id``.

        Used by PATCH /data to auto-create columns for inbound row data whose
        keys are not yet in the artifact's schema — the data-factory row
        endpoint rejects unknown columns, so the column must exist first.
        Adapters implement this via their backend's column transform (for
        ``RowTransformFactoryService`` that is a ``mapping`` rule per column on
        ``POST /api/v1/transformation``). On failure, returns ``success=False``
        with ``error_code``/``message`` populated — callers treat this as a
        terminal op error, NOT a raise.
        """
        ...
