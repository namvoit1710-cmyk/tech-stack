import json
import logging
from typing import Dict

from app.layer2_application.interfaces.csv_json_gateway import (
    CsvJsonGatewayError,
    ICsvJsonGateway,
)
from app.layer2_application.interfaces.settings_provider import IRuntimeSettingsProvider

logger = logging.getLogger(__name__)


def _success_response(message: str, data: Dict) -> Dict:
    return {
        "success": True,
        "message": message,
        "data": data,
        "error": None,
    }


def _error_response(
    message: str, details: str, code: str, status_code: int | None = None
) -> Dict:
    return {
        "success": False,
        "message": message,
        "data": None,
        "error": {
            "code": code,
            "details": details,
            "status_code": status_code,
        },
    }


def convert_csv_to_json(
    file_id: str,
    *,
    csv_json_gateway: ICsvJsonGateway,
    settings_provider: IRuntimeSettingsProvider,
) -> Dict:
    """Convert CSV file to JSON format and upload to file service."""
    if not file_id:
        return _error_response(
            message="Missing file_id",
            details="file_id is required",
            code="MISSING_FILE_ID",
            status_code=None,
        )

    try:
        response_data = csv_json_gateway.fetch_csv_data(file_id)
        logging.info("[CSV2JSON] Received source payload for file_id=%s", file_id)

        json_content = json.dumps(response_data, ensure_ascii=False).encode("utf-8")
        output_name = f"{file_id}.json"
        json_file_id = csv_json_gateway.upload_json_file(output_name, json_content)
        download_url = settings_provider.get_file_download_url(json_file_id)

        return _success_response(
            message="CSV converted to JSON successfully",
            data={
                "file_id": json_file_id,
                "download_url": download_url,
                "original_csv_file_id": file_id,
            },
        )

    except CsvJsonGatewayError as e:
        logging.error("[CSV2JSON] Gateway error (%s): %s", e.code, e.details)
        return _error_response(
            message=e.message,
            details=e.details,
            code=e.code,
            status_code=e.status_code,
        )

    except Exception as e:
        logging.error(f"[CSV2JSON] Unexpected error: {str(e)}")
        return _error_response(
            message="Conversion failed",
            details=str(e),
            code="CONVERSION_FAILED",
            status_code=None,
        )
