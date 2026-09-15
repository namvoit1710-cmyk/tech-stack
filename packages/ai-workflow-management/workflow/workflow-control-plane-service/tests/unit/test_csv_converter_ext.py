"""Tests for CsvConverter extensions: dict_to_csv / csv_to_dict."""

import pytest

from app.layer2_application.data.csv_converter import CsvConverter


class TestDictToCsv:
    def test_flat_dict(self):
        data = {"name": "Alice", "age": 30}
        csv_bytes, filename, row_count = CsvConverter.dict_to_csv(data)

        assert row_count == 1
        assert filename == "data.csv"
        text = csv_bytes.decode("utf-8")
        assert "name" in text
        assert "Alice" in text

    def test_nested_dict_flattened(self):
        data = {"output": {"result": 42}, "status": "ok"}
        csv_bytes, filename, row_count = CsvConverter.dict_to_csv(data)

        text = csv_bytes.decode("utf-8")
        assert "output.result" in text
        assert "42" in text
        assert "status" in text

    def test_list_of_dicts(self):
        data = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ]
        csv_bytes, filename, row_count = CsvConverter.dict_to_csv(data)

        assert row_count == 2
        text = csv_bytes.decode("utf-8")
        assert "Alice" in text
        assert "Bob" in text

    def test_empty_dict(self):
        csv_bytes, filename, row_count = CsvConverter.dict_to_csv({})
        assert csv_bytes == b""
        assert row_count == 0

    def test_empty_list(self):
        csv_bytes, filename, row_count = CsvConverter.dict_to_csv([])
        assert csv_bytes == b""
        assert row_count == 0

    def test_list_value_json_encoded(self):
        data = {"tags": ["a", "b", "c"], "name": "test"}
        csv_bytes, _, _ = CsvConverter.dict_to_csv(data)
        text = csv_bytes.decode("utf-8")
        # JSON array is encoded (may be CSV-quoted)
        assert "a" in text and "b" in text and "c" in text

    def test_none_value(self):
        data = {"key": None, "name": "test"}
        csv_bytes, _, row_count = CsvConverter.dict_to_csv(data)
        assert row_count == 1


class TestCsvToDict:
    def test_single_row(self):
        data = {"name": "Alice", "age": 30}
        csv_bytes, _, _ = CsvConverter.dict_to_csv(data)
        result = CsvConverter.csv_to_dict(csv_bytes)

        assert isinstance(result, dict)
        assert result["name"] == "Alice"
        assert result["age"] == 30

    def test_multiple_rows(self):
        data = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ]
        csv_bytes, _, _ = CsvConverter.dict_to_csv(data)
        result = CsvConverter.csv_to_dict(csv_bytes)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["name"] == "Bob"

    def test_nested_dict_unflattened(self):
        data = {"output": {"result": 42}, "metadata": {"type": "task"}}
        csv_bytes, _, _ = CsvConverter.dict_to_csv(data)
        result = CsvConverter.csv_to_dict(csv_bytes)

        assert result["output"]["result"] == 42
        assert result["metadata"]["type"] == "task"

    def test_list_value_preserved(self):
        data = {"tags": ["a", "b"], "name": "test"}
        csv_bytes, _, _ = CsvConverter.dict_to_csv(data)
        result = CsvConverter.csv_to_dict(csv_bytes)

        assert result["tags"] == ["a", "b"]
        assert result["name"] == "test"

    def test_empty_bytes(self):
        result = CsvConverter.csv_to_dict(b"")
        assert result == {}

    def test_boolean_round_trip(self):
        data = {"active": True, "deleted": False}
        csv_bytes, _, _ = CsvConverter.dict_to_csv(data)
        result = CsvConverter.csv_to_dict(csv_bytes)

        assert result["active"] is True
        assert result["deleted"] is False

    def test_none_round_trip(self):
        data = {"key": None, "name": "test"}
        csv_bytes, _, _ = CsvConverter.dict_to_csv(data)
        result = CsvConverter.csv_to_dict(csv_bytes)

        assert result["key"] is None
        assert result["name"] == "test"


class TestSchemaDrivenTyping:
    """Write-time column_types make reads lossless instead of guessing."""

    def test_infer_column_types(self):
        data = {"code": "0123", "qty": 5, "active": True, "tags": ["a"], "rate": 1.5}
        types = CsvConverter.infer_column_types(data)
        assert types == {
            "code": "string",
            "qty": "number",
            "active": "boolean",
            "tags": "json",
            "rate": "number",
        }

    def test_infer_nested_and_list_rows(self):
        data = [{"user": {"id": "007"}}, {"user": {"id": "008"}}]
        types = CsvConverter.infer_column_types(data)
        assert types["user.id"] == "string"

    def test_id_like_string_preserved_with_schema(self):
        # Without a schema the heuristic mangles "0123" -> 123. With it, the
        # declared "string" column round-trips verbatim.
        data = {"code": "0123", "version": "1.20", "qty": 7}
        csv_bytes, _, _ = CsvConverter.dict_to_csv(data)
        types = CsvConverter.infer_column_types(data)

        guessed = CsvConverter.csv_to_dict(csv_bytes)
        assert guessed["code"] == 123  # heuristic loses the leading zero

        typed = CsvConverter.csv_to_dict(csv_bytes, types)
        assert typed["code"] == "0123"
        assert typed["version"] == "1.20"
        assert typed["qty"] == 7  # numbers still restored

    def test_unknown_column_falls_back_to_heuristic(self):
        # A ref predating the schema (partial/empty column_types) still parses.
        data = {"qty": 5}
        csv_bytes, _, _ = CsvConverter.dict_to_csv(data)
        assert CsvConverter.csv_to_dict(csv_bytes, {})["qty"] == 5


class TestFlattenUnflatten:
    def test_flatten_simple(self):
        data = {"a": {"b": 1, "c": 2}}
        flat = CsvConverter._flatten(data)
        assert flat == {"a.b": 1, "a.c": 2}

    def test_unflatten_simple(self):
        flat = {"a.b": 1, "a.c": 2}
        result = CsvConverter._unflatten(flat)
        assert result == {"a": {"b": 1, "c": 2}}

    def test_flatten_deep_nesting(self):
        data = {"a": {"b": {"c": "deep"}}}
        flat = CsvConverter._flatten(data)
        assert flat == {"a.b.c": "deep"}

    def test_round_trip_deep(self):
        original = {"output": {"nested": {"result": 42}}, "status": "ok"}
        flat = CsvConverter._flatten(original)
        unflat = CsvConverter._unflatten(flat)
        assert unflat["output"]["nested"]["result"] == 42
        assert unflat["status"] == "ok"
