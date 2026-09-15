"""Unit tests for RowTransformFactoryService.

Focus: RowOperation -> row-transform JSON payload mapping (see row-transform.md),
and the response-shape unification into RowTransformResult.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.layer2_application.interfaces.data_factory_client_interface import (
    IDataFactoryClient,
)
from app.layer2_application.interfaces.node_data_factory_service_interface import (
    RowIdentifier,
    RowOperation,
)
from app.layer4_frameworks.services.row_transform_factory_service import (
    RowTransformFactoryService,
)


class _RecordingClient(IDataFactoryClient):
    """Captures the last transform_rows payload and returns a canned response."""

    def __init__(self, response: dict[str, Any] | None = None, raise_exc: Exception | None = None):
        self._response = response or {
            "success": True,
            "file_id": "new-file",
            "affected_rows": 1,
            "total_rows": 5,
            "message": "ok",
        }
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def transform_rows(
        self,
        file_id: str,
        operations: list[dict[str, Any]],
        file_format: str = "csv",
        version_id: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append({
            "file_id": file_id,
            "operations": operations,
            "file_format": file_format,
            "version_id": version_id,
        })
        if self._raise:
            raise self._raise
        return self._response

    async def transform_data(  # pragma: no cover — not used by row service
        self,
        file_id: str,
        rules: list[dict[str, Any]],
        file_format: str = "csv",
        output_format: str = "csv",
    ) -> dict[str, Any]:
        return {"success": True}


# ---------------------------------------------------------------------------
# Payload mapping
# ---------------------------------------------------------------------------


class TestSerializeInsert:
    async def test_insert_before_index_maps_all_fields(self):
        client = _RecordingClient()
        svc = RowTransformFactoryService(client)
        await svc.apply_row_operations(
            file_id="src",
            operations=[RowOperation(
                operation="insert",
                row_identifier=RowIdentifier(type="index", index=2),
                data={"name": "Alice", "age": 30},
                position="before",
            )],
        )
        op = client.calls[0]["operations"][0]
        assert op == {
            "operation": "insert",
            "row_identifier": {"type": "index", "index": 2},
            "data": {"name": "Alice", "age": 30},
            "position": "before",
        }
        assert client.calls[0]["file_id"] == "src"
        assert client.calls[0]["file_format"] == "csv"

    async def test_use_column_indices_flag_emitted_only_when_true(self):
        client = _RecordingClient()
        svc = RowTransformFactoryService(client)
        await svc.apply_row_operations(
            file_id="src",
            operations=[RowOperation(
                operation="insert",
                row_identifier=RowIdentifier(type="index", index=0),
                data={"0": 1, "1": "X"},
                position="at_index",
                use_column_indices=True,
            )],
        )
        assert client.calls[0]["operations"][0]["use_column_indices"] is True

    async def test_use_column_indices_omitted_by_default(self):
        client = _RecordingClient()
        svc = RowTransformFactoryService(client)
        await svc.apply_row_operations(
            file_id="src",
            operations=[RowOperation(
                operation="insert",
                row_identifier=RowIdentifier(type="index", index=0),
                data={"x": 1},
                position="after",
            )],
        )
        assert "use_column_indices" not in client.calls[0]["operations"][0]


class TestSerializeDelete:
    async def test_delete_by_index(self):
        client = _RecordingClient()
        svc = RowTransformFactoryService(client)
        await svc.apply_row_operations(
            file_id="src",
            operations=[RowOperation(
                operation="delete",
                row_identifier=RowIdentifier(type="index", index=3),
            )],
        )
        assert client.calls[0]["operations"][0] == {
            "operation": "delete",
            "row_identifier": {"type": "index", "index": 3},
        }

    async def test_delete_by_condition(self):
        client = _RecordingClient()
        svc = RowTransformFactoryService(client)
        await svc.apply_row_operations(
            file_id="src",
            operations=[RowOperation(
                operation="delete",
                row_identifier=RowIdentifier(
                    type="condition",
                    expression="pl.col('age') > 65",
                ),
            )],
        )
        assert client.calls[0]["operations"][0]["row_identifier"] == {
            "type": "condition",
            "expression": "pl.col('age') > 65",
        }

    async def test_delete_by_values(self):
        client = _RecordingClient()
        svc = RowTransformFactoryService(client)
        await svc.apply_row_operations(
            file_id="src",
            operations=[RowOperation(
                operation="delete",
                row_identifier=RowIdentifier(
                    type="values",
                    match_values={"__row_id": "r_123"},
                ),
            )],
        )
        assert client.calls[0]["operations"][0]["row_identifier"] == {
            "type": "values",
            "match_values": {"__row_id": "r_123"},
        }


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestParseResponse:
    async def test_success_unwraps_all_fields(self):
        client = _RecordingClient({
            "success": True,
            "file_id": "f_new",
            "affected_rows": 2,
            "total_rows": 7,
            "message": "ok",
        })
        svc = RowTransformFactoryService(client)
        result = await svc.apply_row_operations(
            file_id="src",
            operations=[RowOperation(
                operation="delete",
                row_identifier=RowIdentifier(type="index", index=0),
            )],
        )
        assert result.success is True
        assert result.file_id == "f_new"
        assert result.affected_rows == 2
        assert result.total_rows == 7
        assert result.message == "ok"
        assert result.error_code == ""

    async def test_failure_populates_error_code(self):
        client = _RecordingClient({"success": False, "message": "boom"})
        svc = RowTransformFactoryService(client)
        result = await svc.apply_row_operations(
            file_id="src",
            operations=[RowOperation(
                operation="delete",
                row_identifier=RowIdentifier(type="index", index=0),
            )],
        )
        assert result.success is False
        assert result.message == "boom"
        assert result.error_code == "FACTORY_ERROR"

    async def test_success_without_file_id_is_bad_response(self):
        """Row-transform MUST return a file_id for the mutated artifact —
        success + empty file_id is a contract violation."""
        client = _RecordingClient({"success": True, "file_id": ""})
        svc = RowTransformFactoryService(client)
        result = await svc.apply_row_operations(
            file_id="src",
            operations=[RowOperation(
                operation="delete",
                row_identifier=RowIdentifier(type="index", index=0),
            )],
        )
        assert result.success is False
        assert result.error_code == "FACTORY_BAD_RESPONSE"

    async def test_client_exception_surfaces_as_factory_unavailable(self):
        client = _RecordingClient(raise_exc=RuntimeError("network"))
        svc = RowTransformFactoryService(client)
        result = await svc.apply_row_operations(
            file_id="src",
            operations=[RowOperation(
                operation="delete",
                row_identifier=RowIdentifier(type="index", index=0),
            )],
        )
        assert result.success is False
        assert result.error_code == "FACTORY_UNAVAILABLE"
        assert "network" in result.message


# ---------------------------------------------------------------------------
# Version handling (data-factory v2 in-place versioning)
# ---------------------------------------------------------------------------


class TestVersionId:
    async def test_version_id_input_forwarded_to_client(self):
        client = _RecordingClient()
        svc = RowTransformFactoryService(client)
        await svc.apply_row_operations(
            file_id="src",
            operations=[RowOperation(
                operation="delete",
                row_identifier=RowIdentifier(type="index", index=0),
            )],
            version_id="ver-1",
        )
        assert client.calls[0]["version_id"] == "ver-1"

    async def test_version_id_omitted_passes_none(self):
        client = _RecordingClient()
        svc = RowTransformFactoryService(client)
        await svc.apply_row_operations(
            file_id="src",
            operations=[RowOperation(
                operation="delete",
                row_identifier=RowIdentifier(type="index", index=0),
            )],
        )
        assert client.calls[0]["version_id"] is None

    async def test_response_version_id_unwrapped_into_result(self):
        client = _RecordingClient({
            "success": True, "file_id": "src", "version_id": "ver-2",
        })
        svc = RowTransformFactoryService(client)
        result = await svc.apply_row_operations(
            file_id="src",
            operations=[RowOperation(
                operation="delete",
                row_identifier=RowIdentifier(type="index", index=0),
            )],
        )
        assert result.success is True
        assert result.file_id == "src"
        assert result.version_id == "ver-2"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestInputGuards:
    async def test_empty_file_id_rejected(self):
        client = _RecordingClient()
        svc = RowTransformFactoryService(client)
        result = await svc.apply_row_operations(
            file_id="",
            operations=[RowOperation(
                operation="delete",
                row_identifier=RowIdentifier(type="index", index=0),
            )],
        )
        assert result.success is False
        assert result.error_code == "INVALID_INPUT"
        assert client.calls == []

    async def test_empty_operations_rejected(self):
        client = _RecordingClient()
        svc = RowTransformFactoryService(client)
        result = await svc.apply_row_operations(file_id="src", operations=[])
        assert result.success is False
        assert result.error_code == "INVALID_INPUT"
        assert client.calls == []
