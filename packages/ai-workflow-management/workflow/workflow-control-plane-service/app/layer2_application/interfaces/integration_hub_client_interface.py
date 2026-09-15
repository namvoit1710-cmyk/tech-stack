"""Port for the Integration Hub (BDM search-help)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class IIntegrationHubClient(ABC):
    """Provides the Integration Hub base URL and optional search-help reads.

    The base URL is embedded into the generated AutoForm schema's
    ``serverOptions.endpoint`` so the FE fetches value-help rows directly.
    ``fetch_search_help_rows`` exists mainly for server-side validation/tests.
    """

    @property
    @abstractmethod
    def base_url(self) -> str:
        ...

    @abstractmethod
    async def fetch_search_help_rows(
        self,
        *,
        model_id: str,
        bdm_field: str,
        page: int = 1,
        page_size: int = 100,
        context_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fetch one page of raw search-help rows for a BDM field."""
        ...

    @abstractmethod
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
        """Query search-help rows and return a flattened ``{file_id, total_count, data}`` envelope.

        Each row's ``payload`` is spread into a flat record with a ``__row_id``;
        ``rowKey``/metadata are dropped.
        """
        ...
