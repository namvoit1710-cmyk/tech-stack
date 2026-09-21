from __future__ import annotations

from typing import Any, Dict, Protocol


class IFilePreviewGateway(Protocol):
    """Port for file preview and read-only data retrieval from file service."""

    def get_odata_preview(self, file_id: str, num_rows: int) -> Dict[str, Any]:
        """Fetch preview rows via OData endpoint."""

    def query_odata_data(self, file_id: str, odata_query: str = "") -> Dict[str, Any]:
        """Fetch rows via the file service OData data endpoint."""

    def download_file_content(self, file_id: str) -> bytes:
        """Download raw file bytes for CSV fallback parsing."""
