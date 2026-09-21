from __future__ import annotations

from app.layer2_application.interfaces.settings_provider import IRuntimeSettingsProvider


class RuntimeSettingsProvider(IRuntimeSettingsProvider):
    """Layer 4 adapter that exposes app settings to layer 2 via a port."""

    def __init__(self, settings) -> None:
        self._settings = settings

    def get_workflow_api_base_url(self) -> str:
        return str(self._settings.WORKFLOW_API_BASE_URL).rstrip("/")

    def get_schema_transform_query_data_url(self, file_id: str) -> str:
        return str(self._settings.SCHEMA_TRANSFORM_QUERY_DATA_URL).format(
            file_id=file_id
        )

    def get_file_service_upload_url(self) -> str:
        explicit_url = str(self._settings.FILE_SERVICE_UPLOAD_URL or "").rstrip("/")
        if explicit_url:
            return explicit_url
        return f"{self.get_file_service_base_url()}/api/v1/upload"

    def get_file_download_url(self, file_id: str) -> str:
        base_url = self.get_file_service_base_url()
        return f"{base_url}/api/v1/download/{file_id}"

    def get_file_service_base_url(self) -> str:
        return str(self._settings.FILE_URL).rstrip("/")

    def get_request_timeout_seconds(self) -> int:
        return int(self._settings.REQUEST_TIMEOUT_SECONDS)

    def get_default_max_review_iterations(self) -> int:
        return int(self._settings.DEFAULT_MAX_REVIEW_ITERATIONS)

    def get_default_filename(self) -> str:
        return str(self._settings.DEFAULT_FILENAME)

    def get_default_max_workers(self) -> int:
        return int(self._settings.DEFAULT_MAX_WORKERS)

    def get_min_groups_for_parallel(self) -> int:
        return int(self._settings.MIN_GROUPS_FOR_PARALLEL)

    def get_data_factory_url(self) -> str:
        return str(self._settings.DATA_FACTORY_URL)
