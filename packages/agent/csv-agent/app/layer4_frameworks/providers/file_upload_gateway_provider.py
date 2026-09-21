from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, BinaryIO

import requests

from app.layer2_application.interfaces.file_upload_gateway import (
    FileUploadGatewayError,
    IFileUploadGateway,
)

logger = logging.getLogger(__name__)


class FileUploadGatewayProvider(IFileUploadGateway):
    """Layer-4 File Service upload provider."""

    def __init__(self, settings_provider) -> None:
        self._settings_provider = settings_provider
        self._base_url = settings_provider.get_file_service_base_url().rstrip("/")
        self._timeout = settings_provider.get_request_timeout_seconds()

    def upload_file(self, file_path: str) -> dict[str, Any]:
        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            raise FileUploadGatewayError(
                "UPLOAD_FILE_NOT_FOUND", "File to upload was not found", file_path
            )
        try:
            with file_path_obj.open("rb") as file_data:
                return self._post_file(
                    file_obj=file_data,
                    filename=file_path_obj.name,
                    content_type="text/csv",
                )
        except FileUploadGatewayError:
            raise
        except Exception as exc:
            raise FileUploadGatewayError(
                "UPLOAD_FAILED", "Failed to upload file", str(exc)
            ) from exc

    def upload_file_content(
        self,
        file_content: BinaryIO,
        filename: str = "file.csv",
        content_type: str = "text/csv",
    ) -> dict[str, Any]:
        try:
            return self._post_file(
                file_obj=file_content, filename=filename, content_type=content_type
            )
        except FileUploadGatewayError:
            raise
        except Exception as exc:
            raise FileUploadGatewayError(
                "UPLOAD_FAILED", "Failed to upload file content", str(exc)
            ) from exc

    def _post_file(
        self, *, file_obj: BinaryIO, filename: str, content_type: str
    ) -> dict[str, Any]:
        if not self._base_url:
            raise FileUploadGatewayError(
                "UPLOAD_MISSING_BASE_URL",
                "File service URL is not configured",
                "FILE_URL is empty",
            )
        response = requests.post(
            f"{self._base_url}/api/v1/upload",
            files={"file": (filename, file_obj, content_type)},
            timeout=self._timeout,
        )
        if response.status_code >= 400:
            raise FileUploadGatewayError(
                "UPLOAD_HTTP_ERROR",
                "File service upload failed",
                response.text,
                response.status_code,
            )
        try:
            result = response.json()
        except ValueError as exc:
            raise FileUploadGatewayError(
                "UPLOAD_INVALID_JSON",
                "File service upload returned invalid JSON",
                response.text,
                response.status_code,
            ) from exc
        if not isinstance(result, dict):
            raise FileUploadGatewayError(
                "UPLOAD_INVALID_RESPONSE",
                "Upload returned invalid payload",
                str(result),
                response.status_code,
            )
        file_id = result.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            raise FileUploadGatewayError(
                "UPLOAD_MISSING_FILE_ID",
                "Upload succeeded but no file_id returned",
                str(result),
                response.status_code,
            )
        result.setdefault("download_url", f"{self._base_url}/api/v1/download/{file_id}")
        logger.info("[FileUpload] Uploaded %s as %s", filename, file_id)
        return result
