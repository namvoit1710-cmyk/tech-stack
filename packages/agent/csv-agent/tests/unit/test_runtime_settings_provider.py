from types import SimpleNamespace

from app.layer4_frameworks.providers.runtime_settings_provider import (
    RuntimeSettingsProvider,
)


def test_runtime_settings_provider_maps_and_strips_urls():
    provider = RuntimeSettingsProvider(
        SimpleNamespace(
            WORKFLOW_API_BASE_URL="http://workflow/",
            SCHEMA_TRANSFORM_QUERY_DATA_URL="http://schema/{file_id}",
            FILE_SERVICE_UPLOAD_URL="http://files/upload/",
            FILE_URL="http://files/",
            REQUEST_TIMEOUT_SECONDS="30",
            DEFAULT_MAX_REVIEW_ITERATIONS="3",
            DEFAULT_FILENAME="out.csv",
            DEFAULT_MAX_WORKERS="7",
            MIN_GROUPS_FOR_PARALLEL="2",
            DATA_FACTORY_URL="http://mcp",
        )
    )

    assert provider.get_workflow_api_base_url() == "http://workflow"
    assert (
        provider.get_schema_transform_query_data_url("file_1") == "http://schema/file_1"
    )
    assert provider.get_file_service_upload_url() == "http://files/upload"
    assert (
        provider.get_file_download_url("file_1")
        == "http://files/api/v1/download/file_1"
    )
    assert provider.get_request_timeout_seconds() == 30
    assert provider.get_default_max_review_iterations() == 3
    assert provider.get_default_filename() == "out.csv"
    assert provider.get_default_max_workers() == 7
    assert provider.get_min_groups_for_parallel() == 2
    assert provider.get_data_factory_url() == "http://mcp"
