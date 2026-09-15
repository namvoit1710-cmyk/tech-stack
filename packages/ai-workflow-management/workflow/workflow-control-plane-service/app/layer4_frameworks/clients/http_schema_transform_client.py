"""HTTP adapter for the external schema-transform service.

Endpoints:
  POST /api/v1/files/extract-headers  — extract column headers from a file
"""
import logging
from typing import Any

import httpx

from app.layer2_application.interfaces.schema_transform_client_interface import ISchemaTransformClient

logger = logging.getLogger(__name__)


class HttpSchemaTransformClient(ISchemaTransformClient):
    def __init__(self, schema_transform_url: str) -> None:
        self._base_url = schema_transform_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=60.0)

    async def extract_headers(self, file_id: str) -> list[dict[str, Any]]:
        url = f"{self._base_url}/api/v1/files/extract-headers"
        try:
            response = await self._client.post(
                url,
                data={"file_id": file_id},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("result", [])
        except Exception as exc:
            logger.error("POST %s failed: %s", url, exc)
            return []
