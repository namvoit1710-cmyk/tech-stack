from __future__ import annotations

from typing import Any, Protocol


class FileUploadGatewayError(Exception):
    """Domain-safe error raised by file upload infrastructure gateway."""

    def __init__(
        self, code: str, message: str, details: str, status_code: int | None = None
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.status_code = status_code


class IFileUploadGateway(Protocol):
    """Port for uploading local files to file service."""

    def upload_file(self, file_path: str) -> dict[str, Any]:
        """Upload file and return payload containing at least file_id/download_url."""
