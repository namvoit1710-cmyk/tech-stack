"""Use case for calling predefined API actions as internal function."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.file_service_client_interface import IFileServiceClient
from app.layer2_application.interfaces.app_config_interface import IAppConfig
from app.layer2_application.data.csv_converter import CsvConverter


class InternalCallApiUseCase:
    """Call a predefined API action and return result as file_id.
    
    The result is always stored as a JSON file with array of objects format.
    """
    
    def __init__(
        self,
        logger: ILogger,
        file_service_client: IFileServiceClient,
        app_config: IAppConfig | None = None,
    ) -> None:
        self._logger = logger
        self._file_service = file_service_client
        self._app_config = app_config
        self._timeout = 30.0
    
    async def execute(
        self,
        action_config: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute API action and return file_id with result data."""
        
        action_name = action_config.get("name", "unknown")
        method = action_config.get("method", "GET").upper()
        endpoint_url = action_config.get("endpoint_url", "")
        payload_template = action_config.get("payload", {})
        
        if not endpoint_url:
            return {"error": f"Action '{action_name}' has no endpoint_url configured"}
        
        # Build full URL - use internal service URL if relative path
        if endpoint_url.startswith("/"):
            # Relative path - use internal API gateway or control plane URL
            base_url = getattr(self._app_config, "API_GATEWAY_URL", "") or "http://localhost:8000"
            full_url = f"{base_url.rstrip('/')}{endpoint_url}"
        else:
            full_url = endpoint_url
        
        # Merge params into payload
        request_payload = {**payload_template, **(params or {})}
        
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                if method == "GET":
                    response = await client.get(full_url, params=request_payload)
                elif method == "POST":
                    response = await client.post(full_url, json=request_payload)
                elif method == "PUT":
                    response = await client.put(full_url, json=request_payload)
                elif method == "DELETE":
                    response = await client.delete(full_url, params=request_payload)
                else:
                    return {"error": f"Unsupported HTTP method: {method}"}
                
                response.raise_for_status()
                
                # Parse response
                try:
                    response_data = response.json()
                except Exception:
                    response_data = {"raw_text": response.text}
                
        except httpx.HTTPStatusError as exc:
            return {
                "error": f"HTTP error {exc.response.status_code}: {exc.response.text[:500]}",
                "action_name": action_name,
            }
        except Exception as exc:
            return {
                "error": f"Request failed: {str(exc)}",
                "action_name": action_name,
            }
        
        # Normalize to array format
        if isinstance(response_data, dict):
            # Check if it's a paginated response with 'data' or 'items' key
            if "data" in response_data and isinstance(response_data["data"], list):
                output_data = response_data["data"]
            elif "items" in response_data and isinstance(response_data["items"], list):
                output_data = response_data["items"]
            elif "value" in response_data and isinstance(response_data["value"], list):
                output_data = response_data["value"]
            else:
                output_data = [response_data]
        elif isinstance(response_data, list):
            output_data = response_data
        else:
            output_data = [{"value": response_data}]
        
        # Upload to file storage as CSV
        csv_bytes, filename, row_count = CsvConverter.dict_to_csv(output_data)
        filename = f"api_action_{action_name}_result.csv"
        try:
            upload_result = await self._file_service.upload_file(
                file_bytes=csv_bytes,
                filename=filename,
                content_type="text/csv",
            )
        except Exception as exc:
            return {
                "error": f"Failed to upload result to file storage: {str(exc)}",
                "action_name": action_name,
            }
        
        return {
            "file_id": upload_result.get("file_id"),
            "record_count": row_count,
            "action_name": action_name,
            "status": "success",
        }
