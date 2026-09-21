import csv
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.layer2_application.features import convert_json_to_csv as module


class _UploadGateway:
    def __init__(self):
        self.uploaded_path = ""

    def upload_file(self, file_path: str):
        self.uploaded_path = file_path
        return {
            "file_id": "file_uploaded",
            "download_url": "http://files/api/v1/download/file_uploaded",
        }


def _provider(*, min_groups=99, max_workers=4, default_filename="default.csv"):
    return SimpleNamespace(
        get_min_groups_for_parallel=lambda: min_groups,
        get_default_max_workers=lambda: max_workers,
        get_default_filename=lambda: default_filename,
    )


def _read_csv(path: str):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def test_json_helpers_cover_control_chars_lists_scalars_and_wrappers():
    assert module.sanitize_json_string(123) == 123
    assert module.sanitize_json_string(" a\x01b\x00 ") == "a\\u0001b"

    with pytest.raises(ValueError):
        module.extract_first_valid_json("   ")

    extracted = module.extract_first_valid_json('{"text": "a\\"b"} trailing')
    assert json.loads(extracted) == {"text": 'a"b'}

    assert module.flatten_json({"results": [{"nested": {"x": 1}}]}) == [{"nested_x": 1}]
    assert module.flatten_json({"items": [1, None, math.nan]}) == [
        {"value": 1},
        {"value": ""},
        {"value": ""},
    ]
    assert module.flatten_json(5) == [{"value": 5}]
    assert module.flatten_json({"tags": ["a", "b"]}) == [{"tags": "['a', 'b']"}]
    assert module.flatten_json({"data": [{"x": 1}]}, max_level=1) == [{"x": 1}]

    with pytest.raises(ValueError):
        module.flatten_json("{bad json")


def test_create_csv_from_json_empty_creates_empty_temp_file():
    path = module.create_csv_from_json([])
    try:
        assert Path(path).exists()
        assert Path(path).read_text(encoding="utf-8") == ""
    finally:
        os.remove(path)


def test_process_single_group_empty_dict_multi_and_invalid():
    assert module.process_single_group("to_Empty", [])[3] == ["to_Empty"]

    result = module.process_single_group("to_One", {"name": "A"})
    assert result[2] == ["name"]
    assert result[3] == ["to_One.name"]
    assert result[4] is False

    result = module.process_single_group("to_Many", [{"name": "A"}, {"name": "B"}])
    assert result[3] == ["to_Many.name0", "to_Many.name1"]
    assert result[4] is True

    with pytest.raises(ValueError):
        module.process_single_group("to_Bad", "not-a-list-or-dict")


def test_create_csv_with_grouped_headers_parallel_and_no_column_paths():
    path = module.create_csv_with_grouped_headers(
        {
            "id": 10,
            "to_Description": [{"language": "EN"}, {"language": "DE"}],
            "to_Unit": {"unit": "EA"},
        },
        settings_provider=_provider(min_groups=2, max_workers=2),
    )
    try:
        rows = _read_csv(path)
        assert rows[0] == [
            "main_table.id",
            "to_Description.language0",
            "to_Description.language1",
            "to_Unit.unit",
        ]
        assert rows[1] == ["10", "EN", "DE", "EA"]
    finally:
        os.remove(path)

    empty_path = module.create_csv_with_grouped_headers(
        {}, settings_provider=_provider()
    )
    try:
        assert Path(empty_path).read_text(encoding="utf-8") == ""
    finally:
        os.remove(empty_path)


def test_create_csv_with_grouped_headers_sequential_error_path():
    with pytest.raises(ValueError):
        module.create_csv_with_grouped_headers(
            {"to_Bad": "bad"}, settings_provider=_provider(min_groups=99)
        )


def test_sanitize_filename_uses_default_for_empty_sanitized_name():
    assert (
        module.sanitize_filename("../***", default_filename="fallback.csv") == "___.csv"
    )
    assert (
        module.sanitize_filename("/", default_filename="fallback.csv") == "fallback.csv"
    )


def test_convert_json_to_csv_service_missing_default_gateway_and_empty_string_paths(
    monkeypatch,
):
    assert module.convert_json_to_csv_service(
        None,
        "out.csv",
        file_upload_gateway=_UploadGateway(),
        settings_provider=_provider(),
    ) == {"error": "Missing json_data"}

    assert module.convert_json_to_csv_service(
        "\x00",
        "out.csv",
        file_upload_gateway=_UploadGateway(),
        settings_provider=_provider(),
    ) == {"error": "Empty JSON string after sanitization"}

    gateway = _UploadGateway()

    result = module.convert_json_to_csv_service(
        {"a": 1},
        "",
        file_upload_gateway=gateway,
        settings_provider=_provider(default_filename="default.csv"),
    )

    assert result["file_id"] == "file_uploaded"
    assert gateway.uploaded_path
    assert os.path.exists(gateway.uploaded_path) is False


def test_convert_json_to_csv_service_empty_flattened_data(monkeypatch):
    monkeypatch.setattr(module, "flatten_json", lambda *args, **kwargs: [])

    result = module.convert_json_to_csv_service(
        {"a": 1},
        "out.csv",
        file_upload_gateway=_UploadGateway(),
        settings_provider=_provider(),
    )

    assert result["error"] == "No data to convert"


def test_convert_json_to_csv_service_grouped_without_main_data_and_value_error(
    monkeypatch,
):
    gateway = _UploadGateway()

    result = module.convert_json_to_csv_service(
        {"to_Description": [{"language": "EN"}]},
        "out.csv",
        file_upload_gateway=gateway,
        settings_provider=_provider(),
    )

    assert result["sections"] == 1
    assert result["columns_count"] == 0
    assert result["rows_converted"] == 1

    bad = module.convert_json_to_csv_service(
        {"to_Bad": "bad"},
        "out.csv",
        file_upload_gateway=_UploadGateway(),
        settings_provider=_provider(),
    )
    assert bad == {"error": "JSON processing error"}


def test_convert_json_to_csv_service_outer_json_decode_and_cleanup_warning(monkeypatch):
    original_create_csv_from_json = module.create_csv_from_json

    def _raise_json_decode_error(_data):
        raise json.JSONDecodeError("bad", "doc", 0)

    monkeypatch.setattr(module, "create_csv_from_json", _raise_json_decode_error)
    result = module.convert_json_to_csv_service(
        {"a": 1},
        "out.csv",
        file_upload_gateway=_UploadGateway(),
        settings_provider=_provider(),
    )
    assert result["error"] == "Invalid JSON format"

    monkeypatch.setattr(module, "create_csv_from_json", original_create_csv_from_json)
    gateway = _UploadGateway()
    original_remove = os.remove

    def _raise_os_error(_path):
        raise OSError("cleanup failed")

    monkeypatch.setattr(module.os, "remove", _raise_os_error)
    result = module.convert_json_to_csv_service(
        {"a": 1},
        "out.csv",
        file_upload_gateway=gateway,
        settings_provider=_provider(),
    )

    assert result["file_id"] == "file_uploaded"
    assert os.path.exists(gateway.uploaded_path)
    original_remove(gateway.uploaded_path)
