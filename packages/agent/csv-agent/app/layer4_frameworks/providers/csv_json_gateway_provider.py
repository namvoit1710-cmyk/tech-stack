from __future__ import annotations

from typing import Any

import requests

from app.layer2_application.interfaces.csv_json_gateway import (
    CsvJsonGatewayError,
    ICsvJsonGateway,
)


class CsvJsonGatewayProvider(ICsvJsonGateway):
    """Layer 4 adapter for CSV-to-JSON infrastructure operations."""

    def __init__(self, settings_provider) -> None:
        self._settings_provider = settings_provider

    def fetch_csv_data(self, file_id: str) -> Any:
        url = self._settings_provider.get_schema_transform_query_data_url(file_id)
        try:
            with requests.get(url, timeout=60) as response:
                if response.status_code != 200:
                    raise CsvJsonGatewayError(
                        code="FETCH_CSV_DATA_FAILED",
                        message="Failed to fetch CSV data",
                        details=response.text,
                        status_code=response.status_code,
                    )
                payload = response.json()
                return payload.get("data") if isinstance(payload, dict) else payload
        except CsvJsonGatewayError:
            raise
        except requests.RequestException as exc:
            raise CsvJsonGatewayError(
                code="REQUEST_EXCEPTION",
                message="Request failed",
                details=str(exc),
                status_code=None,
            ) from exc

    def upload_json_file(self, file_name: str, content: bytes) -> str:
        upload_url = self._settings_provider.get_file_service_upload_url()
        try:
            files = {
                "file": (file_name, content, "application/json"),
            }
            response = requests.post(upload_url, files=files, timeout=30)
            if response.status_code >= 400:
                raise CsvJsonGatewayError(
                    code="UPLOAD_JSON_FAILED",
                    message="Failed to upload JSON file",
                    details=response.text,
                    status_code=response.status_code,
                )

            upload_result = response.json()
            json_file_id = upload_result.get("file_id")
            if not json_file_id:
                raise CsvJsonGatewayError(
                    code="UPLOAD_MISSING_FILE_ID",
                    message="Upload succeeded but no file_id returned",
                    details=str(upload_result),
                    status_code=response.status_code,
                )
            return str(json_file_id)
        except CsvJsonGatewayError:
            raise
        except requests.RequestException as exc:
            raise CsvJsonGatewayError(
                code="REQUEST_EXCEPTION",
                message="Request failed",
                details=str(exc),
                status_code=None,
            ) from exc
