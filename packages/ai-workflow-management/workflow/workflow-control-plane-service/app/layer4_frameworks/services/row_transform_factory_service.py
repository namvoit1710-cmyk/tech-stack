"""Row-transform adapter for ``INodeDataFactoryService``.

Translates neutral ``RowOperation`` dataclasses into the JSON payload shape
consumed by the data-factory's ``POST /api/v1/transformation/rows`` endpoint
(see row-transform.md) and unwraps the HTTP response into a
``RowTransformResult``.

Future alternate backends (different vendor, local Polars compute, streaming
pipeline) live as sibling classes under this package; the use case stays
unchanged.
"""
from __future__ import annotations

import logging
from typing import Any

from app.layer2_application.interfaces.data_factory_client_interface import (
    IDataFactoryClient,
)
from app.layer2_application.interfaces.node_data_factory_service_interface import (
    INodeDataFactoryService,
    RowOperation,
    RowTransformResult,
)

_logger = logging.getLogger(__name__)


class RowTransformFactoryService(INodeDataFactoryService):
    def __init__(self, data_factory_client: IDataFactoryClient) -> None:
        self._client = data_factory_client

    async def apply_row_operations(
        self,
        file_id: str,
        operations: list[RowOperation],
        file_format: str = "csv",
        version_id: str | None = None,
    ) -> RowTransformResult:
        if not file_id:
            return RowTransformResult(
                success=False,
                message="file_id is required",
                error_code="INVALID_INPUT",
            )
        if not operations:
            return RowTransformResult(
                success=False,
                message="at least one row operation is required",
                error_code="INVALID_INPUT",
            )

        payload_ops = [self._serialize_op(op) for op in operations]
        try:
            raw = await self._client.transform_rows(
                file_id=file_id,
                operations=payload_ops,
                file_format=file_format,
                version_id=version_id,
            )
        except Exception as exc:  # pragma: no cover — network-level safety net
            _logger.exception("row-transform factory call failed")
            return RowTransformResult(
                success=False,
                message=str(exc),
                error_code="FACTORY_UNAVAILABLE",
            )

        return self._parse_response(raw)

    async def add_columns(
        self,
        file_id: str,
        columns: list[str],
        *,
        default: Any = "",
        version_id: str | None = None,
    ) -> RowTransformResult:
        if not file_id:
            return RowTransformResult(
                success=False,
                message="file_id is required",
                error_code="INVALID_INPUT",
            )
        cols = [c for c in columns if c]
        if not cols:
            # Nothing to add — echo the input identity unchanged.
            return RowTransformResult(
                success=True, file_id=file_id, version_id=version_id or "",
            )

        # One ``mapping`` rule per new column. ``pl.lit(<default>)`` creates the
        # column filled with the default without referencing any existing
        # column, so it can't fail on schema mismatch. The transform endpoint
        # reads the file's latest version; in a PATCH the caller's
        # ``version_id`` always points at that latest version (each op produces
        # a new latest), so no explicit pin is needed.
        literal = _polars_literal(default)
        rules = [
            {
                "rule_name": f"add column {col}",
                "type": "mapping",
                "params": {"expression": f"pl.lit({literal})", "new_col": col},
            }
            for col in cols
        ]
        try:
            raw = await self._client.transform_data(
                file_id=file_id,
                rules=rules,
                file_format="csv",
                output_format="csv",
            )
        except Exception as exc:  # pragma: no cover — network-level safety net
            _logger.exception("data-factory add_columns call failed")
            return RowTransformResult(
                success=False,
                message=str(exc),
                error_code="FACTORY_UNAVAILABLE",
            )

        return self._parse_transform_response(raw, fallback_file_id=file_id)

    @staticmethod
    def _parse_transform_response(
        raw: dict[str, Any], *, fallback_file_id: str,
    ) -> RowTransformResult:
        """Unwrap a ``POST /api/v1/transformation`` response.

        The deployed factory uses the v2 in-place version model: identity comes
        back as ``result.output_file_id`` (stable, == source) +
        ``result.output_version_id`` (new revision). Older shapes that echo a
        top-level ``file_id`` are tolerated; ``fallback_file_id`` is the source
        id the caller passed (file identity is stable across a column add).
        """
        if not isinstance(raw, dict):
            return RowTransformResult(
                success=False,
                message="factory returned non-dict response",
                error_code="FACTORY_BAD_RESPONSE",
                raw={"response": raw},
            )
        result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        success = bool(raw.get("success", False)) and bool(
            result.get("success", True)
        )
        file_id = str(
            result.get("output_file_id")
            or raw.get("file_id")
            or fallback_file_id
            or ""
        )
        version_id = str(
            result.get("output_version_id") or raw.get("version_id") or ""
        )
        message = str(raw.get("message") or result.get("message") or "")
        if not success:
            return RowTransformResult(
                success=False,
                file_id=file_id,
                version_id=version_id,
                message=message or "data-factory column transform failed",
                error_code=str(raw.get("error_code") or "FACTORY_ERROR"),
                raw=raw,
            )
        if not file_id:
            return RowTransformResult(
                success=False,
                message="column transform response missing file_id",
                error_code="FACTORY_BAD_RESPONSE",
                raw=raw,
            )
        return RowTransformResult(
            success=True,
            file_id=file_id,
            version_id=version_id,
            total_rows=int(result.get("total_rows", 0) or 0),
            message=message,
            raw=raw,
        )

    @staticmethod
    def _serialize_op(op: RowOperation) -> dict[str, Any]:
        """Convert a ``RowOperation`` into the row-transform JSON schema.

        Strips ``None`` fields so the payload matches the API spec in
        row-transform.md (server expects absent keys rather than nulls).
        """
        identifier = {"type": op.row_identifier.type}
        if op.row_identifier.index is not None:
            identifier["index"] = op.row_identifier.index
        if op.row_identifier.expression is not None:
            identifier["expression"] = op.row_identifier.expression
        if op.row_identifier.match_values is not None:
            identifier["match_values"] = op.row_identifier.match_values

        serialized: dict[str, Any] = {
            "operation": op.operation,
            "row_identifier": identifier,
        }
        if op.data is not None:
            serialized["data"] = op.data
        if op.position is not None:
            serialized["position"] = op.position
        if op.use_column_indices:
            serialized["use_column_indices"] = True
        return serialized

    @staticmethod
    def _parse_response(raw: dict[str, Any]) -> RowTransformResult:
        if not isinstance(raw, dict):
            return RowTransformResult(
                success=False,
                message="factory returned non-dict response",
                error_code="FACTORY_BAD_RESPONSE",
                raw={"response": raw},
            )
        success = bool(raw.get("success", False))
        message = str(raw.get("message", "") or "")
        file_id = str(raw.get("file_id", "") or "")
        version_id = str(raw.get("version_id", "") or "")
        affected = int(raw.get("affected_rows", 0) or 0)
        total = int(raw.get("total_rows", 0) or 0)
        error_code = "" if success else (
            raw.get("error_code") or "FACTORY_ERROR"
        )
        if success and not file_id:
            return RowTransformResult(
                success=False,
                message="factory response missing file_id",
                error_code="FACTORY_BAD_RESPONSE",
                raw=raw,
            )
        return RowTransformResult(
            success=success,
            file_id=file_id,
            version_id=version_id,
            affected_rows=affected,
            total_rows=total,
            message=message,
            error_code=error_code,
            raw=raw,
        )


def _polars_literal(value: Any) -> str:
    """Render ``value`` as a Polars literal argument for ``pl.lit(...)``.

    Used to backfill a newly-added column. Empty / None → empty string; strings
    are single-quoted with single quotes and backslashes escaped (the factory
    evaluates the expression as Polars code, so the literal must be valid).
    """
    if value is None or value == "":
        return "''"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"
