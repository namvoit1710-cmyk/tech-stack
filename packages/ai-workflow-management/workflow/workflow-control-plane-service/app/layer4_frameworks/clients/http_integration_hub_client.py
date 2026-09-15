"""HTTP adapter for the Integration Hub (BDM search-help).

The generated AutoForm schema embeds the search-help endpoint so the FE
fetches value-help rows directly; this client mainly exposes the base URL the
schema builder needs, plus a server-side ``fetch_search_help_rows`` helper for
validation/tests.
"""
import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.layer1_domain.value_objects.csv_document import ROW_ID_COLUMN
from app.layer2_application.interfaces.integration_hub_client_interface import (
    IIntegrationHubClient,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


def flatten_search_help_response(
    response: dict[str, Any],
    *,
    file_id: str = "",
) -> dict[str, Any]:
    """Flatten a search-help ``rows`` response into a table-data envelope.

    The Integration Hub returns rows whose real values are nested under
    ``payload`` (``{ totalCount, items: [{ rowKey, rowSeq, payload: {...} }] }``).
    This pure transform spreads each row's ``payload`` into a flat record,
    prepends ``__row_id`` (the row's ``rowSeq`` when present, else a 1-based
    index), and drops ``rowKey``/``rowSeq``/metadata. The result matches the
    ``{file_id, total_count, data}`` shape used elsewhere; ``file_id`` is
    passed through verbatim (no file is created here).
    """
    items = response.get("items") or []
    data: list[dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        payload = item.get("payload")
        row: dict[str, Any] = {ROW_ID_COLUMN: str(item.get("rowSeq", idx))}
        if isinstance(payload, dict):
            row.update(payload)
        data.append(row)
    total = response.get("totalCount")
    if not isinstance(total, int):
        total = len(data)
    return {"file_id": file_id, "total_count": total, "data": data}


class HttpIntegrationHubClient(IIntegrationHubClient):
    """HTTP client for the Integration Hub search-help API."""

    def __init__(self, integration_hub_api_url: str) -> None:
        self._base_url = integration_hub_api_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)

    @property
    def base_url(self) -> str:
        return self._base_url

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Call on application shutdown."""
        await self._client.aclose()

    async def fetch_search_help_rows(
        self,
        *,
        model_id: str,
        bdm_field: str,
        page: int = 1,
        page_size: int = 100,
        context_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = (
            f"{self._base_url}/api/v1/business-data-models/{model_id}"
            f"/bdm-fields/{bdm_field}/search-help/rows"
        )
        params: dict[str, Any] = {"page": page, "pageSize": page_size}
        if context_values:
            params["contextValues"] = quote(json.dumps(context_values))
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {"items": data}

    async def query_search_help_data(
        self,
        *,
        model_id: str,
        bdm_field: str,
        page: int = 1,
        page_size: int = 100,
        context_values: dict[str, Any] | None = None,
        file_id: str = "",
    ) -> dict[str, Any]:
        """Query search-help rows and return the flattened table-data envelope.

        Fetches one page via :meth:`fetch_search_help_rows`, then flattens it
        with :func:`flatten_search_help_response` into
        ``{file_id, total_count, data}``.
        """
        response = await self.fetch_search_help_rows(
            model_id=model_id,
            bdm_field=bdm_field,
            page=page,
            page_size=page_size,
            context_values=context_values,
        )
        return flatten_search_help_response(response, file_id=file_id)
