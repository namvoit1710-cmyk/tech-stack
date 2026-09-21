from __future__ import annotations

import pytest

from app.layer2_application.features import transform_column as transform_column_module
from app.layer2_application.features import transform_row as transform_row_module
from app.layer2_application.features.transform_column import transform_column
from app.layer2_application.features.transform_row import transform_row


class _Settings:
    def get_file_download_url(self, file_id: str) -> str:
        return f"http://files.local/download/{file_id}"


class _StructuredGateway:
    def __init__(self) -> None:
        self.column_calls = []
        self.row_calls = []

    async def generate_column_transform_payload(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        verification_prompt: str,
        file_id: str,
    ):
        self.column_calls.append(file_id)
        return {
            "file_path": file_id,
            "file_format": "csv",
            "rules": [
                {
                    "rule_name": "rename_name",
                    "type": "rename_columns",
                    "params": {"mapping": {"Name": "FullName"}},
                }
            ],
        }

    async def generate_row_transform_payload(
        self, *, system_prompt: str, user_prompt: str, file_id: str
    ):
        self.row_calls.append(file_id)
        return {
            "file_path": file_id,
            "file_format": "csv",
            "operations": [
                {
                    "operation": "delete",
                    "row_identifier": {"type": "index", "index": 0},
                }
            ],
        }


class _DataFactoryGateway:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.column_calls = []
        self.row_calls = []

    async def transform_file(self, **kwargs):
        self.column_calls.append(kwargs)
        return self.result

    async def transform_rows(self, **kwargs):
        self.row_calls.append(kwargs)
        return self.result


def _patch_common(monkeypatch):
    monkeypatch.setattr(
        transform_column_module,
        "get_first_n_rows_impl",
        lambda file_id, num_rows, **kwargs: [{"Name": "Alice", "Age": "30"}],
    )
    monkeypatch.setattr(
        transform_row_module,
        "get_first_n_rows_impl",
        lambda file_id, num_rows, **kwargs: [{"Name": "Alice", "Age": "30"}],
    )


@pytest.mark.parametrize(
    ("mcp_result", "expected_file_id"),
    [
        (
            {
                "success": True,
                "result": {
                    "result_file_url": "http://files.local/download/file_nested"
                },
            },
            "file_nested",
        ),
        (
            {
                "success": True,
                "result": {
                    "odata": {
                        "result_file_url": "http://files.local/download/file_odata"
                    }
                },
            },
            "file_odata",
        ),
        (
            {
                "success": True,
                "output_file_url": "http://files.local/download/file_output",
            },
            "file_output",
        ),
        (
            {
                "success": True,
                "result_file_url": "http://files.local/download/file_root",
            },
            "file_root",
        ),
    ],
)
@pytest.mark.asyncio
async def test_transform_column_extracts_all_supported_result_url_shapes(
    monkeypatch, mcp_result, expected_file_id
):
    _patch_common(monkeypatch)

    result = await transform_column(
        long_input="rename Name to FullName",
        file_id="file_input",
        structured_llm_gateway=_StructuredGateway(),
        data_factory_gateway=_DataFactoryGateway(mcp_result),
        file_preview_gateway=object(),
        settings_provider=_Settings(),
    )

    assert result["file_id"] == expected_file_id
    assert result["download_url"].endswith(expected_file_id)


@pytest.mark.parametrize(
    ("mcp_result", "expected_file_id"),
    [
        (
            {
                "success": True,
                "result": {
                    "result_file_url": "http://files.local/download/file_nested"
                },
            },
            "file_nested",
        ),
        (
            {
                "success": True,
                "result": {
                    "odata": {
                        "result_file_url": "http://files.local/download/file_odata"
                    }
                },
            },
            "file_odata",
        ),
        (
            {
                "success": True,
                "output_file_url": "http://files.local/download/file_output",
            },
            "file_output",
        ),
        (
            {
                "success": True,
                "result_file_url": "http://files.local/download/file_root",
            },
            "file_root",
        ),
    ],
)
@pytest.mark.asyncio
async def test_transform_row_extracts_all_supported_result_url_shapes(
    monkeypatch, mcp_result, expected_file_id
):
    _patch_common(monkeypatch)

    result = await transform_row(
        long_input="delete first row",
        file_id="file_input",
        structured_llm_gateway=_StructuredGateway(),
        data_factory_gateway=_DataFactoryGateway(mcp_result),
        file_preview_gateway=object(),
        settings_provider=_Settings(),
    )

    assert result["file_id"] == expected_file_id
    assert result["download_url"].endswith(expected_file_id)


@pytest.mark.asyncio
async def test_transform_column_uses_explicit_injected_gateways(monkeypatch):
    _patch_common(monkeypatch)
    structured = _StructuredGateway()
    data_factory = _DataFactoryGateway(
        {"success": True, "download_url": "http://files.local/download/file_configured"}
    )

    result = await transform_column(
        long_input="rename Name",
        file_id="file_input",
        structured_llm_gateway=structured,
        data_factory_gateway=data_factory,
        file_preview_gateway=object(),
        settings_provider=_Settings(),
    )

    assert result["file_id"] == "file_configured"
    assert structured.column_calls == ["file_input"]
    assert data_factory.column_calls


@pytest.mark.asyncio
async def test_transform_row_uses_explicit_injected_gateways(monkeypatch):
    _patch_common(monkeypatch)
    structured = _StructuredGateway()
    data_factory = _DataFactoryGateway(
        {"success": True, "download_url": "http://files.local/download/file_configured"}
    )

    result = await transform_row(
        long_input="delete first row",
        file_id="file_input",
        structured_llm_gateway=structured,
        data_factory_gateway=data_factory,
        file_preview_gateway=object(),
        settings_provider=_Settings(),
    )

    assert result["file_id"] == "file_configured"
    assert structured.row_calls == ["file_input"]
    assert data_factory.row_calls
