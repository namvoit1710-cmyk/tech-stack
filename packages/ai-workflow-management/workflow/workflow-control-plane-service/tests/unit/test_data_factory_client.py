"""Unit tests for HttpDataFactoryClient v2 row-transform integration.

Data-factory v2 mutates the source file in place and addresses the new
revision by ``version_id``, encoded in ``download_url`` as
``.../api/v1/files/versions/{version_id}/download``. The response no longer
carries a ``file_id``; the client echoes the (stable) input file_id as the
artifact identity and surfaces ``version_id`` as the new revision.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.layer4_frameworks.clients.http_data_factory_client import (
    HttpDataFactoryClient,
    _extract_version_id,
)


class TestExtractVersionId:
    def test_v2_version_download_url(self):
        url = "https://factory/api/v1/files/versions/ver_019e72ea/download"
        assert _extract_version_id(url) == "ver_019e72ea"

    def test_path_only_input(self):
        assert _extract_version_id("/api/v1/files/versions/ver_abc/download") == "ver_abc"

    def test_trailing_slash_tolerated(self):
        assert _extract_version_id("https://factory/files/versions/ver_abc/download/") == "ver_abc"

    def test_no_versions_segment_yields_empty(self):
        # Legacy ".../download/{id}" shape carries no version segment.
        assert _extract_version_id("https://factory/api/v1/download/abc-123") == ""

    def test_versions_as_last_segment_yields_empty(self):
        assert _extract_version_id("https://factory/api/v1/files/versions") == ""

    def test_empty_string(self):
        assert _extract_version_id("") == ""


class _FakeResponse:
    def __init__(self, payload: dict, *, raise_exc: Exception | None = None):
        self._payload = payload
        self._raise = raise_exc

    def raise_for_status(self) -> None:
        if self._raise:
            raise self._raise

    def json(self) -> dict:
        return self._payload


@pytest.mark.asyncio
class TestTransformRows:
    async def _client_returning(self, payload, *, raise_exc=None):
        client = HttpDataFactoryClient("https://factory")
        client._client.post = AsyncMock(
            return_value=_FakeResponse(payload, raise_exc=raise_exc),
        )
        return client

    async def test_v2_response_echoes_file_id_and_surfaces_version(self):
        # v2: success, no file_id, download_url addresses a version. The
        # client echoes the input file_id (stable identity) and extracts
        # version_id from the URL.
        client = await self._client_returning({
            "success": True,
            "download_url": "https://factory/api/v1/files/versions/ver_99/download",
        })
        data = await client.transform_rows(file_id="src", operations=[{"op": "x"}])
        assert data["file_id"] == "src"
        assert data["version_id"] == "ver_99"

    async def test_explicit_response_fields_pass_through_untouched(self):
        client = await self._client_returning({
            "success": True, "file_id": "real-id", "version_id": "ver_explicit",
            "download_url": "https://factory/api/v1/files/versions/ver_other/download",
        })
        data = await client.transform_rows(file_id="src", operations=[{"op": "x"}])
        # Explicit file_id + version_id present → neither echo nor scrape runs.
        assert data["file_id"] == "real-id"
        assert data["version_id"] == "ver_explicit"

    async def test_500_returns_failure_without_file_id(self):
        client = await self._client_returning(
            {}, raise_exc=RuntimeError("500 Internal Server Error"),
        )
        data = await client.transform_rows(file_id="src", operations=[{"op": "x"}])
        assert data["success"] is False
        assert "file_id" not in data


@pytest.mark.asyncio
class TestTransformRowsPayload:
    """v2 row-transform request shape (see ROW_TRANSFORMATION_API_DOCS.md)."""

    async def _capture(self):
        client = HttpDataFactoryClient("https://factory")
        post = AsyncMock(return_value=_FakeResponse({"success": True, "file_id": "f"}))
        client._client.post = post
        return client, post

    async def test_payload_uses_file_id_and_omits_legacy_and_version(self):
        client, post = await self._capture()
        await client.transform_rows(file_id="src", operations=[{"op": "x"}])
        payload = post.call_args.kwargs["json"]
        assert payload["file_id"] == "src"
        assert payload["operations"] == [{"op": "x"}]
        assert payload["file_format"] == "csv"
        # v2 schema drops the legacy file_path mirror, and an absent
        # version_id tells the factory to read the latest version.
        assert "file_path" not in payload
        assert "version_id" not in payload

    async def test_payload_pins_version_id_when_provided(self):
        client, post = await self._capture()
        await client.transform_rows(
            file_id="src", operations=[{"op": "x"}], version_id="v-42",
        )
        payload = post.call_args.kwargs["json"]
        assert payload["version_id"] == "v-42"
