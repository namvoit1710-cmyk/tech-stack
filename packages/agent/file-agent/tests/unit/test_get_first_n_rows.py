from types import SimpleNamespace
from unittest.mock import MagicMock


def test_get_first_n_rows_impl_downloads_first_line_when_preview_is_empty(monkeypatch):
    from app.layer2_application.features import get_first_n_rows as get_first_n_rows_module

    monkeypatch.setattr(
        get_first_n_rows_module,
        "AppSettingsAdapter",
        lambda: SimpleNamespace(FILE_URL="https://files.example"),
    )

    odata_response = MagicMock()
    odata_response.raise_for_status = MagicMock()
    odata_response.json.return_value = {"file_id": "file-123", "total_matches": 0, "data": []}

    download_response = MagicMock()
    download_response.raise_for_status = MagicMock()
    download_response.content = b"col1,col2\n1,2\n"

    captured_urls = []

    def fake_get(url, **kwargs):
        captured_urls.append(url)
        if len(captured_urls) == 1:
            return odata_response
        return download_response

    monkeypatch.setattr(get_first_n_rows_module.requests, "get", fake_get)

    result = get_first_n_rows_module._get_first_n_rows_impl("file-123", 10)

    assert captured_urls == [
        "https://files.example/api/v1/odata/file-123/data?$top=10&$skip=0&$count=true",
        "https://files.example/api/v1/download/file-123",
    ]
    assert result == {
        "file_id": "file-123",
        "total_matches": 1,
        "data": ["col1,col2"],
    }