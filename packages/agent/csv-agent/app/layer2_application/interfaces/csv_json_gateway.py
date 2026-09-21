from __future__ import annotations

from typing import Any, Protocol


class CsvJsonGatewayError(Exception):
    """Domain-safe error raised by CSV/JSON infrastructure gateway."""

    def __init__(
        self, code: str, message: str, details: str, status_code: int | None = None
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.status_code = status_code


class ICsvJsonGateway(Protocol):
    """Port for CSV data fetch and JSON file upload operations."""

    def fetch_csv_data(self, file_id: str) -> Any:
        """Fetch CSV data payload from upstream service."""

    def upload_json_file(self, file_name: str, content: bytes) -> str:
        """Upload JSON content and return resulting file_id."""
