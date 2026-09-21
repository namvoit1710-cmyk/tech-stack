from types import SimpleNamespace
from unittest.mock import Mock

from app.layer2_application.interfaces.csv_json_gateway import CsvJsonGatewayError


def _load_module():
    import app.layer2_application.features.convert_csv_to_json as module

    return module


def _mock_settings_provider():
    return SimpleNamespace(
        get_file_download_url=lambda file_id: f"http://file-service/api/v1/download/{file_id}",
    )


def _mock_gateway(
    fetch_data=None,
    upload_file_id="file_json_001",
    error: CsvJsonGatewayError | None = None,
):
    if error is not None:
        return SimpleNamespace(
            fetch_csv_data=Mock(side_effect=error),
            upload_json_file=Mock(),
        )
    return SimpleNamespace(
        fetch_csv_data=Mock(
            return_value=fetch_data
            if fetch_data is not None
            else [{"id": 1}, {"id": 2}]
        ),
        upload_json_file=Mock(return_value=upload_file_id),
    )


def test_convert_csv_to_json_missing_file_id():
    module = _load_module()
    result = module.convert_csv_to_json(
        "",
        csv_json_gateway=_mock_gateway(),
        settings_provider=_mock_settings_provider(),
    )

    assert result["success"] is False
    assert result["message"] == "Missing file_id"
    assert result["data"] is None
    assert result["error"]["code"] == "MISSING_FILE_ID"


def test_convert_csv_to_json_fetch_error():
    module = _load_module()
    gateway_error = CsvJsonGatewayError(
        code="FETCH_CSV_DATA_FAILED",
        message="Failed to fetch CSV data",
        details="upstream error",
        status_code=500,
    )

    result = module.convert_csv_to_json(
        "file_123",
        csv_json_gateway=_mock_gateway(error=gateway_error),
        settings_provider=_mock_settings_provider(),
    )

    assert result["success"] is False
    assert result["message"] == "Failed to fetch CSV data"
    assert result["error"]["status_code"] == 500
    assert "upstream error" in result["error"]["details"]


def test_convert_csv_to_json_success():
    module = _load_module()
    gateway = _mock_gateway(
        fetch_data=[{"id": 1}, {"id": 2}], upload_file_id="file_json_001"
    )

    result = module.convert_csv_to_json(
        "file_123",
        csv_json_gateway=gateway,
        settings_provider=_mock_settings_provider(),
    )

    assert result["success"] is True
    assert result["message"] == "CSV converted to JSON successfully"
    assert result["error"] is None
    assert result["data"]["file_id"] == "file_json_001"
    assert (
        result["data"]["download_url"]
        == "http://file-service/api/v1/download/file_json_001"
    )
    assert result["data"]["original_csv_file_id"] == "file_123"
    gateway.fetch_csv_data.assert_called_once_with("file_123")
    gateway.upload_json_file.assert_called_once()
