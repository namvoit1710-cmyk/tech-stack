"""Resolve a frontend-side ``row_path`` to a chain of durable ``__row_id``s.

``row_path`` is the mechanism PATCH ops use to target rows in nested tables.
Each hop carries ``{artifact_id, row_index, filter}`` — a view-level coordinate
captured from the FE grid (filter + row position). The resolver walks the hops
root → leaf, issuing bounded file-service queries at each level, and returns
the resolved ``__row_id`` per hop. Nested hops automatically scope their filter
with ``$filter=__parent_row_id eq '<previous __row_id>'``.

Only ``__row_id`` is authoritative for the actual mutation; the incoming
``row_index`` + ``filter`` is view-only and may become stale. The enclosing
PATCH flow compares the node's ETag against ``If-Match`` to detect cases where
a row moved under the user; this resolver is responsible only for the per-hop
lookup.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl

from app.layer1_domain.entities.data_reference import find_artifact
from app.layer2_application.interfaces.file_service_client_interface import (
    IFileServiceClient,
)

ROW_ID_COLUMN = "__row_id"
PARENT_ROW_ID_COLUMN = "__parent_row_id"


class RowNotFoundError(Exception):
    """Raised when a hop's ``row_index`` resolves to no row under its filter."""

    def __init__(self, hop_index: int, artifact_id: str, row_index: int, reason: str = "") -> None:
        self.hop_index = hop_index
        self.artifact_id = artifact_id
        self.row_index = row_index
        self.reason = reason
        super().__init__(
            f"row_path[{hop_index}] artifact={artifact_id} row_index={row_index}: "
            f"{reason or 'no row at that index under the supplied filter'}"
        )


class ArtifactNotInTreeError(Exception):
    """Raised when a hop references an ``artifact_id`` missing from the tree."""

    def __init__(self, hop_index: int, artifact_id: str) -> None:
        self.hop_index = hop_index
        self.artifact_id = artifact_id
        super().__init__(
            f"row_path[{hop_index}] references artifact_id='{artifact_id}' "
            f"not present in the node's __file_ref tree"
        )


class RowPathResolver:
    """Walks a ``row_path`` against the file service.

    The resolver is stateless; callers instantiate once and reuse across ops.
    """

    def __init__(self, file_service_client: IFileServiceClient) -> None:
        self._fs = file_service_client

    async def resolve(
        self,
        file_ref: dict[str, Any],
        row_path: list[dict[str, Any]],
    ) -> list[str]:
        """Walk ``row_path`` root → leaf and return a list of resolved ``__row_id``s.

        - ``row_path`` is a list of hops, each ``{artifact_id, row_index, filter}``.
        - Returns one ``__row_id`` per hop, in order.
        - Raises ``ArtifactNotInTreeError`` if any hop's artifact isn't in the tree.
        - Raises ``RowNotFoundError`` if any hop's row_index is out of range
          under its (possibly parent-scoped) filter.
        """
        resolved: list[str] = []
        previous_row_id: str | None = None

        for i, hop in enumerate(row_path):
            artifact_id = hop.get("artifact_id", "")
            row_index = int(hop.get("row_index", 0))
            fe_filter = hop.get("filter", "") or ""

            artifact = find_artifact(file_ref, artifact_id)
            if artifact is None:
                raise ArtifactNotInTreeError(hop_index=i, artifact_id=artifact_id)

            query_params = self._build_query_params(
                fe_filter=fe_filter,
                parent_row_id=previous_row_id,
                row_index=row_index,
            )

            row_id = await self._fetch_row_id(
                file_id=artifact.get("file_id", ""),
                query_params=query_params,
                hop_index=i,
                artifact_id=artifact_id,
                row_index=row_index,
            )
            resolved.append(row_id)
            previous_row_id = row_id

        return resolved

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_query_params(
        self,
        *,
        fe_filter: str,
        parent_row_id: str | None,
        row_index: int,
    ) -> dict[str, str]:
        """Parse the FE-supplied OData filter, add parent scoping + paging.

        Accepts full-string OData (``"$filter=x eq 'y'&$orderby=sku asc"``) OR an
        empty string. The paging params (``$skip``, ``$top``, ``$select``) are
        always overwritten by this resolver.
        """
        params: dict[str, str] = {}
        # FE filter may be a full query string. Parse it without URL-decoding
        # filter values; treat it as a spec of OData tokens.
        parsed = parse_qsl(fe_filter, keep_blank_values=False)
        for key, value in parsed:
            params[key] = value

        # Prepend parent scope to $filter (AND-combined).
        if parent_row_id:
            parent_clause = f"{PARENT_ROW_ID_COLUMN} eq '{parent_row_id}'"
            existing = params.get("$filter")
            params["$filter"] = f"({existing}) and ({parent_clause})" if existing else parent_clause

        params["$skip"] = str(row_index)
        params["$top"] = "1"
        params["$select"] = ROW_ID_COLUMN
        return params

    async def _fetch_row_id(
        self,
        *,
        file_id: str,
        query_params: dict[str, str],
        hop_index: int,
        artifact_id: str,
        row_index: int,
    ) -> str:
        result = await self._fs.query_file_data(file_id, query_params)
        rows = result.get("data") or result.get("value") or []
        if not rows:
            raise RowNotFoundError(
                hop_index=hop_index,
                artifact_id=artifact_id,
                row_index=row_index,
            )
        row = rows[0]
        row_id = row.get(ROW_ID_COLUMN, "") if isinstance(row, dict) else ""
        if not row_id:
            raise RowNotFoundError(
                hop_index=hop_index,
                artifact_id=artifact_id,
                row_index=row_index,
                reason=f"row present but missing {ROW_ID_COLUMN} column",
            )
        return row_id
