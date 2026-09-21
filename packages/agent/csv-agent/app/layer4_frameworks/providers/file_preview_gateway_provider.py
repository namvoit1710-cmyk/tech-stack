from __future__ import annotations

from typing import Any, Dict
from urllib.parse import parse_qsl

import requests

from app.layer2_application.interfaces.file_preview_gateway import IFilePreviewGateway


class FilePreviewGatewayProvider(IFilePreviewGateway):
    """Layer 4 adapter for file preview, download, and read-only OData queries."""

    def __init__(self, settings_provider) -> None:
        self._settings_provider = settings_provider

    def _file_service_base_url(self) -> str:
        return self._settings_provider.get_file_service_base_url().rstrip("/")

    def get_odata_preview(self, file_id: str, num_rows: int) -> Dict[str, Any]:
        safe_num_rows = 0
        try:
            safe_num_rows = max(0, int(num_rows))
        except (TypeError, ValueError):
            safe_num_rows = 0

        return self.query_odata_data(
            file_id=file_id,
            odata_query=f"$top={safe_num_rows}&$skip=0&$count=true",
        )

    def query_odata_data(self, file_id: str, odata_query: str = "") -> Dict[str, Any]:
        base_url = self._file_service_base_url()
        timeout = self._settings_provider.get_request_timeout_seconds()
        url = f"{base_url}/api/v1/odata/{file_id}/data"

        query = (odata_query or "").strip()
        if query.startswith("?"):
            query = query[1:]

        params = parse_qsl(query, keep_blank_values=True)
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()

        payload = resp.json()
        if isinstance(payload, dict):
            payload.setdefault("file_id", file_id)
            return payload

        return {
            "file_id": file_id,
            "total_matches": 0,
            "data": [],
        }

    def download_file_content(self, file_id: str) -> bytes:
        base_url = self._file_service_base_url()
        timeout = self._settings_provider.get_request_timeout_seconds()
        download_url = f"{base_url}/api/v1/download/{file_id}"
        resp = requests.get(download_url, timeout=timeout)
        resp.raise_for_status()
        return bytes(resp.content or b"")
