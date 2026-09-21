from typing import Any

from agent_sdk import tool

from app.layer2_application.features.convert_json_to_csv import (
    convert_json_to_csv_service as _impl,
)
from app.layer2_application.interfaces.file_upload_gateway import IFileUploadGateway
from app.layer2_application.interfaces.settings_provider import IRuntimeSettingsProvider


def make_convert_json_to_csv_tool(
    *,
    file_upload_gateway: IFileUploadGateway,
    settings_provider: IRuntimeSettingsProvider,
):
    @tool(internal=True)
    def convert_json_to_csv(
        json_data: Any = None, filename: str = "converted_data.csv"
    ) -> dict:
        """
        Convert nested JSON data to CSV format and upload to file service.

        This tool flattens deeply nested JSON structures into tabular CSV format.
        It automatically sanitizes JSON by removing invalid control characters.

        Automatic format detection:
        - JSON containing fields starting with 'to_*' creates grouped CSV headers.
        - Other JSON creates a standard flat CSV with nested objects flattened.

        Features:
        - Handles nested dictionaries and arrays
        - Handles mixed data types, nulls, and empty objects
        - Automatically extracts records from common wrapper objects
        - Uploads the generated CSV to the file service

        Args:
            json_data: Nested JSON string or structure to convert.
            filename: Output CSV filename. Defaults to "converted_data.csv".

        Returns:
            {
                "file_id": str,
                "download_url": str,
                "message": str,
                "row_count": int
            }
        """
        return _impl(
            json_data=json_data,
            filename=filename,
            file_upload_gateway=file_upload_gateway,
            settings_provider=settings_provider,
        )

    return convert_json_to_csv


__all__ = ["make_convert_json_to_csv_tool"]
