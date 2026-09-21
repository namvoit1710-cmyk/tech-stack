import csv
import os
from types import SimpleNamespace

import pytest

from app.layer2_application.features import convert_json_to_csv as module


def _provider(*, min_groups=99, max_workers=2, default_filename="default.csv"):
    return SimpleNamespace(
        get_min_groups_for_parallel=lambda: min_groups,
        get_default_max_workers=lambda: max_workers,
        get_default_filename=lambda: default_filename,
    )


def test_sanitize_and_extract_json_helpers():
    assert module.sanitize_json_string(' {"a":"b\x00c"}\n') == '{"a":"bc"}'
    assert module.sanitize_json_string(123) == 123
    assert module.extract_first_valid_json('{"a": "}"} trailing') == '{"a": "}"}'
    assert module.extract_first_valid_json('[{"a": 1}] trailing') == '[{"a": 1}]'
    assert module.extract_first_valid_json("not-json") == "not-json"
    with pytest.raises(ValueError):
        module.extract_first_valid_json("")


def test_flatten_json_handles_wrappers_scalars_and_invalid_json():
    assert module.flatten_json({"data": [{"a": {"b": 1}, "c": None}]}) == [
        {"a_b": 1, "c": ""}
    ]
    assert module.flatten_json({"results": [1, 2]}) == [{"value": 1}, {"value": 2}]
    assert module.flatten_json({"items": [{"x": float("nan")}]}) == [{"x": ""}]
    with pytest.raises(ValueError):
        module.flatten_json("{bad json")


def test_process_single_group_variants():
    name, rows, source_columns, flat_names, is_multi = module.process_single_group(
        "to_Items", [{"id": 1}, {"id": 2}]
    )
    assert name == "to_Items"
    assert rows == [{"id": 1}, {"id": 2}]
    assert source_columns == ["id"]
    assert flat_names == ["to_Items.id0", "to_Items.id1"]
    assert is_multi is True
    assert module.process_single_group("to_Empty", [])[3] == ["to_Empty"]
    with pytest.raises(ValueError):
        module.process_single_group("to_Bad", object())


def test_create_csv_from_json_and_grouped_headers_cleanup():
    flat_path = module.create_csv_from_json([{"a": 1}, {"b": 2}])
    grouped_path = module.create_csv_with_grouped_headers(
        {"id": 1, "to_Text": [{"language": "EN", "text": "Hi"}], "to_Empty": []},
        settings_provider=_provider(),
    )
    try:
        with open(flat_path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        assert rows[0] == ["a", "b"]

        with open(grouped_path, newline="", encoding="utf-8") as fh:
            grouped_rows = list(csv.reader(fh))
        assert "main_table.id" in grouped_rows[0]
        assert "to_Text.language" in grouped_rows[0]
    finally:
        os.remove(flat_path)
        os.remove(grouped_path)


def test_convert_json_to_csv_service_handles_grouped_headers():
    class _Upload:
        def upload_file(self, path):
            assert os.path.exists(path)
            return {
                "file_id": "file_csv",
                "download_url": "http://files/download/file_csv",
            }

    result = module.convert_json_to_csv_service(
        {"id": 1, "to_Description": [{"language": "EN"}, {"language": "DE"}]},
        "../unsafe name",
        file_upload_gateway=_Upload(),
        settings_provider=_provider(),
    )

    assert result["file_id"] == "file_csv"
    assert result["sections"] == 1
    assert result["rows_converted"] == 2
