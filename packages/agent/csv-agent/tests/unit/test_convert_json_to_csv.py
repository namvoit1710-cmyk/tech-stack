import os

from app.layer2_application.features.convert_json_to_csv import (
    convert_json_to_csv_service,
)


class _UploadGatewayOk:
    def __init__(self):
        self.uploaded_path = ""
        self.exists_during_upload = False

    def upload_file(self, file_path: str):
        self.uploaded_path = file_path
        self.exists_during_upload = os.path.exists(file_path)
        return {
            "file_id": "file_csv_123",
            "download_url": "http://file-service/api/v1/download/file_csv_123",
        }


class _SettingsProvider:
    def get_default_filename(self):
        return "converted_data.csv"

    def get_min_groups_for_parallel(self):
        return 99

    def get_default_max_workers(self):
        return 2


class _UploadGatewayFail:
    def upload_file(self, file_path: str):
        raise RuntimeError("secret-token-leak")


def test_convert_json_to_csv_service_success_and_cleanup():
    gateway = _UploadGatewayOk()

    result = convert_json_to_csv_service(
        json_data={"name": "A", "meta": {"id": 10}},
        filename="output.csv",
        file_upload_gateway=gateway,
        settings_provider=_SettingsProvider(),
    )

    assert result["file_id"] == "file_csv_123"
    assert result["rows_converted"] == 1
    assert result["columns_count"] == 2
    assert gateway.exists_during_upload is True
    assert os.path.exists(gateway.uploaded_path) is False


def test_convert_json_to_csv_service_invalid_json_is_safe_response():
    result = convert_json_to_csv_service(
        json_data="{invalid_json",
        filename="output.csv",
        file_upload_gateway=_UploadGatewayOk(),
        settings_provider=_SettingsProvider(),
    )

    assert result["error"] == "Invalid JSON format"
    assert "details" not in result


def test_convert_json_to_csv_service_does_not_leak_exception_text():
    result = convert_json_to_csv_service(
        json_data={"name": "A"},
        filename="output.csv",
        file_upload_gateway=_UploadGatewayFail(),
        settings_provider=_SettingsProvider(),
    )

    assert result["error"] == "Conversion failed"
    assert "details" not in result
    assert "secret-token-leak" not in str(result)


def test_convert_json_to_csv_service_handles_special_chars_in_nested_payload():
    gateway = _UploadGatewayOk()

    result = convert_json_to_csv_service(
        json_data={
            "title": 'Item "A", line 1',
            "to_Description": [
                {
                    "language": "EN",
                    "articleDescription": "First line\nSecond line",
                }
            ],
        },
        filename="output.csv",
        file_upload_gateway=gateway,
        settings_provider=_SettingsProvider(),
    )

    assert result["file_id"] == "file_csv_123"
    assert gateway.exists_during_upload is True
