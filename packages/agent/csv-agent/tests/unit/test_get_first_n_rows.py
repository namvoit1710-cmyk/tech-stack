from types import SimpleNamespace


def test_get_first_n_rows_impl_downloads_rows_when_preview_is_empty():
    from app.layer2_application.features import get_first_n_rows as module

    gateway = SimpleNamespace(
        get_odata_preview=lambda file_id, num_rows: {
            "file_id": "file-123",
            "total_matches": 0,
            "data": [],
        },
        download_file_content=lambda file_id: b"col1,col2\n1,2\n3,4\n",
    )

    result = module.get_first_n_rows_impl("file-123", 10, file_preview_gateway=gateway)

    assert result == {
        "file_id": "file-123",
        "total_matches": 2,
        "data": [
            {"col1": "1", "col2": "2"},
            {"col1": "3", "col2": "4"},
        ],
    }


def test_get_first_n_rows_impl_returns_preview_without_download():
    from app.layer2_application.features import get_first_n_rows as module

    calls = {"download": 0}

    def _download(_file_id):
        calls["download"] += 1
        return b""

    gateway = SimpleNamespace(
        get_odata_preview=lambda file_id, num_rows: {
            "file_id": file_id,
            "data": [{"id": "1"}],
        },
        download_file_content=_download,
    )

    assert module.get_first_n_rows_impl(
        "file-123", "bad", file_preview_gateway=gateway
    ) == {
        "file_id": "file-123",
        "data": [{"id": "1"}],
    }
    assert calls["download"] == 0


def test_get_first_n_rows_impl_handles_non_dict_preview_and_bad_num_rows():
    from app.layer2_application.features import get_first_n_rows as module

    gateway = SimpleNamespace(
        get_odata_preview=lambda file_id, num_rows: ["unexpected"],
        download_file_content=lambda file_id: b"col1\n1\n2\n",
    )

    assert module.get_first_n_rows_impl(
        "file-123", object(), file_preview_gateway=gateway
    ) == {
        "file_id": "file-123",
        "total_matches": 0,
        "data": [],
    }
