from __future__ import annotations

from typing import Protocol


class IRuntimeSettingsProvider(Protocol):
    """Layer 2 settings port used by features/tools/graph logic."""

    def get_workflow_api_base_url(self) -> str:
        """Return workflow API base URL without trailing slash."""

    def get_schema_transform_query_data_url(self, file_id: str) -> str:
        """Return schema-transform query URL for a given file id."""

    def get_file_service_upload_url(self) -> str:
        """Return file-service upload URL without trailing slash."""

    def get_file_download_url(self, file_id: str) -> str:
        """Return file download URL for a given file id."""

    def get_file_service_base_url(self) -> str:
        """Return base file service URL without trailing slash."""

    def get_request_timeout_seconds(self) -> int:
        """Return request timeout in seconds."""

    def get_default_max_review_iterations(self) -> int:
        """Return default max review loop iterations."""

    def get_default_filename(self) -> str:
        """Return default filename for generated CSV files."""

    def get_default_max_workers(self) -> int:
        """Return default max workers for parallel processing."""

    def get_min_groups_for_parallel(self) -> int:
        """Return minimum group count to enable parallel processing."""

    def get_data_factory_url(self) -> str:
        """Return Data Factory MCP endpoint URL."""
