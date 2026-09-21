from __future__ import annotations

import pytest

from app.layer1_domain.exceptions import (
    MCPCommunicationError,
    TransformationError,
)
from app.layer2_application.features import transform_column as transform_column_module
from app.layer2_application.features.transform_column import transform_column


class _Settings:
    def get_file_download_url(self, file_id: str) -> str:
        return f"http://files.local/download/{file_id}"


class _StructuredColumnGateway:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload or {
            "file_path": "file_input",
            "file_format": "csv",
            "rules": [
                {
                    "rule_name": "rename_name",
                    "type": "rename_columns",
                    "params": {"mapping": {"Name": "FullName"}},
                }
            ],
        }
        self.error = error
        self.calls: list[dict] = []

    async def generate_column_transform_payload(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        verification_prompt: str,
        file_id: str,
    ):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "verification_prompt": verification_prompt,
                "file_id": file_id,
            }
        )
        if self.error is not None:
            raise self.error
        payload = dict(self.payload)
        payload["file_path"] = file_id
        return payload


class _ColumnDataFactoryGateway:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def transform_file(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _patch_preview_and_settings(monkeypatch):
    monkeypatch.setattr(
        transform_column_module,
        "get_first_n_rows_impl",
        lambda file_id, num_rows, **kwargs: [{"Name": "Alice", "Age": "30"}],
    )


@pytest.mark.asyncio
async def test_transform_column_success_builds_download_url_from_file_id(monkeypatch):
    _patch_preview_and_settings(monkeypatch)
    data_factory = _ColumnDataFactoryGateway(
        {"success": True, "message": "ok", "file_id": "file_new"}
    )

    result = await transform_column(
        long_input="rename Name to FullName",
        file_id="file_input",
        structured_llm_gateway=_StructuredColumnGateway(),
        data_factory_gateway=data_factory,
        file_preview_gateway=object(),
        settings_provider=_Settings(),
    )

    assert result["file_id"] == "file_new"
    assert result["download_url"] == "http://files.local/download/file_new"
    assert data_factory.calls[0]["file_path"] == "file_input"
    assert data_factory.calls[0]["rules"][0]["type"] == "rename_columns"


@pytest.mark.asyncio
async def test_transform_column_success_extracts_file_id_from_download_url(monkeypatch):
    _patch_preview_and_settings(monkeypatch)
    data_factory = _ColumnDataFactoryGateway(
        {
            "success": True,
            "message": "ok",
            "download_url": "http://files.local/download/file_from_url",
        }
    )

    result = await transform_column(
        long_input="rename Name to FullName",
        file_id="file_input",
        structured_llm_gateway=_StructuredColumnGateway(),
        data_factory_gateway=data_factory,
        file_preview_gateway=object(),
        settings_provider=_Settings(),
    )

    assert result["file_id"] == "file_from_url"
    assert result["download_url"].endswith("/file_from_url")


@pytest.mark.asyncio
async def test_transform_column_wraps_preview_failure(monkeypatch):
    monkeypatch.setattr(
        transform_column_module,
        "get_first_n_rows_impl",
        lambda file_id, num_rows, **kwargs: (_ for _ in ()).throw(
            RuntimeError("preview boom")
        ),
    )

    with pytest.raises(TransformationError) as exc_info:
        await transform_column(
            long_input="rename Name to FullName",
            file_id="file_input",
            structured_llm_gateway=_StructuredColumnGateway(),
            data_factory_gateway=_ColumnDataFactoryGateway({"success": True}),
            file_preview_gateway=object(),
            settings_provider=_Settings(),
        )

    assert exc_info.value.operation == "get_preview_rows"
    assert "preview boom" in str(exc_info.value)


@pytest.mark.asyncio
async def test_transform_column_invalid_llm_json_raises_transformation_error(
    monkeypatch,
):
    _patch_preview_and_settings(monkeypatch)

    with pytest.raises(TransformationError) as exc_info:
        await transform_column(
            long_input="rename Name to FullName",
            file_id="file_input",
            structured_llm_gateway=_StructuredColumnGateway(
                error=ValueError("bad payload")
            ),
            data_factory_gateway=_ColumnDataFactoryGateway({"success": True}),
            file_preview_gateway=object(),
            settings_provider=_Settings(),
        )

    assert exc_info.value.operation == "llm_processing"
    assert "bad payload" in str(exc_info.value)


@pytest.mark.parametrize(
    "mcp_result",
    [
        {"error": "bad root"},
        {"success": False, "message": "bad success"},
        {"success": True, "result": {"success": False, "message": "bad nested"}},
    ],
)
@pytest.mark.asyncio
async def test_transform_column_mcp_error_patterns_raise_domain_error(
    monkeypatch, mcp_result
):
    _patch_preview_and_settings(monkeypatch)

    with pytest.raises(MCPCommunicationError):
        await transform_column(
            long_input="rename Name to FullName",
            file_id="file_input",
            structured_llm_gateway=_StructuredColumnGateway(),
            data_factory_gateway=_ColumnDataFactoryGateway(mcp_result),
            file_preview_gateway=object(),
            settings_provider=_Settings(),
        )


@pytest.mark.asyncio
async def test_transform_column_missing_download_url_raises_transformation_error(
    monkeypatch,
):
    _patch_preview_and_settings(monkeypatch)

    with pytest.raises(TransformationError) as exc_info:
        await transform_column(
            long_input="rename Name to FullName",
            file_id="file_input",
            structured_llm_gateway=_StructuredColumnGateway(),
            data_factory_gateway=_ColumnDataFactoryGateway(
                {"success": True, "message": "ok"}
            ),
            file_preview_gateway=object(),
            settings_provider=_Settings(),
        )

    assert exc_info.value.operation == "extract_result"
