"""HTTP adapter for the Governance API.

Fetches change request approval process data from the governance service.
Uses a reusable ``httpx.AsyncClient`` held on the client instance.
"""
import logging
from typing import Any

import httpx

from app.layer1_domain.template_schema.models import TemplateFieldConfig
from app.layer2_application.interfaces.governance_client_interface import IGovernanceClient

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class HttpGovernanceClient(IGovernanceClient):
    """HTTP client for Governance API."""

    def __init__(self, governance_api_url: str) -> None:
        self._base_url = governance_api_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Call on application shutdown."""
        await self._client.aclose()

    async def get_approval_process(self, cr_id: str) -> list[dict[str, Any]]:
        """Fetch approval process steps for a change request.

        Args:
            cr_id: The change request ID.

        Returns:
            List of approval step dictionaries. Returns empty list on failure.
        """
        url = f"{self._base_url}/api/v1/change-request/get-approval-process?changeRequestId={cr_id}"
        
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            data = response.json()

            # Handle response envelope if present
            if isinstance(data, dict):
                if "data" in data:
                    return data["data"]
                if "items" in data:
                    return data["items"]
                if "value" in data:
                    return data["value"]

            if isinstance(data, list):
                return data

            logger.warning("Unexpected response format from governance API: %s", type(data))
            return []

        except httpx.HTTPStatusError as exc:
            logger.error(
                "Governance API HTTP error %s for CR %s: %s",
                exc.response.status_code,
                cr_id,
                exc.response.text[:500],
            )
            return []
        except Exception as exc:
            logger.error("Failed to fetch approval process for CR %s: %s", cr_id, exc)
            return []

    async def get_template_fields(self, template_id: str) -> list[TemplateFieldConfig]:
        """Fetch a template's field configuration.

        Calls ``GET /api/v1/templates/{template_id}/fields`` and maps each
        entry onto :class:`TemplateFieldConfig`. Raises ``FileNotFoundError``
        when the template is unknown; raises ``httpx.HTTPStatusError`` /
        ``RuntimeError`` on other failures so the node fails loudly rather
        than rendering an empty table.
        """
        url = f"{self._base_url}/api/v1/templates/{template_id}/fields"
        response = await self._client.get(url)

        if response.status_code == 404:
            raise FileNotFoundError(f"Template not found: {template_id!r}")
        response.raise_for_status()

        data = response.json()
        if isinstance(data, dict):
            if "success" in data and not data.get("success", False):
                raise RuntimeError(
                    f"Governance API returned unsuccessful response: {data}"
                )
            items = data.get("data", data.get("items", data.get("value", [])))
        elif isinstance(data, list):
            items = data
        else:
            logger.warning(
                "Unexpected response format from governance fields API: %s",
                type(data),
            )
            items = []

        return [
            TemplateFieldConfig.from_api(item)
            for item in items
            if isinstance(item, dict)
        ]
