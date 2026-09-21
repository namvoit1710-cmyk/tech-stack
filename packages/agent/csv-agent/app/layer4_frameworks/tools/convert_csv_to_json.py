"""Layer-4 tool factory for CSV -> JSON conversion."""

import logging

from agent_sdk import tool

from app.layer2_application.features.convert_csv_to_json import (
    convert_csv_to_json as _impl,
)
from app.layer2_application.interfaces.csv_json_gateway import ICsvJsonGateway
from app.layer2_application.interfaces.settings_provider import IRuntimeSettingsProvider

logger = logging.getLogger(__name__)


def make_convert_csv_to_json_tool(
    *,
    csv_json_gateway: ICsvJsonGateway,
    settings_provider: IRuntimeSettingsProvider,
):
    @tool(internal=True)
    def convert_csv_to_json(file_id: str) -> dict:
        """
        Convert CSV file to JSON format and upload to file service.

        Args:
            file_id: The CSV file ID to convert.

        Returns:
            {
                "success": bool,
                "message": str,
                "data": {
                    "file_id": str,
                    "download_url": str,
                    "original_csv_file_id": str
                } | None,
                "error": dict | None
            }
        """
        logger.info("[INTERNAL-TOOL] convert_csv_to_json called (pure Python, no MCP)")
        return _impl(
            file_id=file_id,
            csv_json_gateway=csv_json_gateway,
            settings_provider=settings_provider,
        )

    return convert_csv_to_json


__all__ = ["make_convert_csv_to_json_tool"]
