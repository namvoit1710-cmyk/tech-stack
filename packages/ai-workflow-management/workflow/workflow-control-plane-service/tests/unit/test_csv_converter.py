"""Tests for CsvConverter domain service."""

import csv
import io
import pytest

from app.layer2_application.data.csv_converter import CsvConverter


class TestSingleRowToCsv:

    def test_basic(self):
        data = {"name": "John", "age": "30", "city": "NYC"}
        result = CsvConverter.single_row_to_csv(data)
        reader = csv.DictReader(io.StringIO(result.decode("utf-8")))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["name"] == "John"
        assert rows[0]["age"] == "30"
        assert rows[0]["city"] == "NYC"

    def test_empty_dict(self):
        result = CsvConverter.single_row_to_csv({})
        assert result == b""

    def test_none_value(self):
        data = {"name": "Alice", "email": None}
        result = CsvConverter.single_row_to_csv(data)
        reader = csv.DictReader(io.StringIO(result.decode("utf-8")))
        rows = list(reader)
        assert rows[0]["email"] == ""

    def test_special_chars(self):
        data = {"name": 'John "Johnny" Doe', "note": "hello, world"}
        result = CsvConverter.single_row_to_csv(data)
        reader = csv.DictReader(io.StringIO(result.decode("utf-8")))
        rows = list(reader)
        assert rows[0]["name"] == 'John "Johnny" Doe'
        assert rows[0]["note"] == "hello, world"

    def test_file_id_in_value(self):
        data = {"name": "John", "invoice": "file-abc-123"}
        result = CsvConverter.single_row_to_csv(data)
        reader = csv.DictReader(io.StringIO(result.decode("utf-8")))
        rows = list(reader)
        assert rows[0]["invoice"] == "file-abc-123"


class TestMultiRowToCsv:

    def test_basic(self):
        rows_data = [
            {"name": "Alice", "age": "25"},
            {"name": "Bob", "age": "30"},
        ]
        result = CsvConverter.multi_row_to_csv(rows_data)
        reader = csv.DictReader(io.StringIO(result.decode("utf-8")))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["name"] == "Alice"
        assert rows[1]["name"] == "Bob"

    def test_with_explicit_columns(self):
        rows_data = [
            {"name": "Alice", "age": "25", "extra": "x"},
        ]
        result = CsvConverter.multi_row_to_csv(rows_data, columns=["name", "age"])
        reader = csv.DictReader(io.StringIO(result.decode("utf-8")))
        rows = list(reader)
        assert list(rows[0].keys()) == ["name", "age"]

    def test_empty_list(self):
        result = CsvConverter.multi_row_to_csv([])
        assert result == b""

    def test_missing_keys(self):
        rows_data = [
            {"name": "Alice"},
            {"name": "Bob", "age": "30"},
        ]
        result = CsvConverter.multi_row_to_csv(rows_data, columns=["name", "age"])
        reader = csv.DictReader(io.StringIO(result.decode("utf-8")))
        rows = list(reader)
        assert rows[0]["age"] == ""
        assert rows[1]["age"] == "30"


class TestDetermineCsvInput:

    def test_csv_single_separates_locked(self):
        resolved = {"name": "John", "method": "GET", "url": "https://api"}
        locked = {"method": "GET", "url": "https://api"}
        csv_bytes, filename = CsvConverter.determine_csv_input(resolved, locked, "csv_single")
        assert filename == "form_data.csv"
        reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["name"] == "John"
        assert "method" not in rows[0]
        assert "url" not in rows[0]

    def test_csv_multi_finds_list_dict(self):
        resolved = {
            "method": "POST",
            "rows": [
                {"name": "Alice", "age": "25"},
                {"name": "Bob", "age": "30"},
            ],
        }
        locked = {"method": "POST"}
        csv_bytes, filename = CsvConverter.determine_csv_input(resolved, locked, "csv_multi")
        assert filename == "rows.csv"
        reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
        rows = list(reader)
        assert len(rows) == 2

    def test_csv_multi_fallback_single_row(self):
        resolved = {"name": "John", "method": "POST"}
        locked = {"method": "POST"}
        csv_bytes, filename = CsvConverter.determine_csv_input(resolved, locked, "csv_multi")
        assert filename == "form_data.csv"
        reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["name"] == "John"

    def test_json_mode_returns_empty(self):
        csv_bytes, filename = CsvConverter.determine_csv_input({"a": "b"}, {}, "json")
        assert csv_bytes == b""

    def test_all_locked_no_csv_fields(self):
        resolved = {"method": "GET"}
        locked = {"method": "GET"}
        csv_bytes, _ = CsvConverter.determine_csv_input(resolved, locked, "csv_single")
        assert csv_bytes == b""


class TestLargeCellSizeLimit:
    """A single CSV cell must round-trip up to the configured 65 MB ceiling.

    Node outputs (e.g. a large response_body) are stored as one CSV cell; the
    csv module's default 128 KB per-field read limit would otherwise raise
    ``_csv.Error: field larger than field limit`` on read-back.
    """

    def test_field_size_limit_is_at_least_65mb(self):
        # csv.field_size_limit is process-global; importing the converter
        # raises it. Guards against anyone lowering the ceiling below 65 MB.
        assert csv.field_size_limit() >= 65 * 1024 * 1024

    def test_large_cell_round_trips(self):
        # A ~65 MB single-cell value survives dict_to_csv -> csv_to_dict.
        big = "x" * (65 * 1024 * 1024)
        csv_bytes, _, rows = CsvConverter.dict_to_csv({"blob": big})
        assert rows == 1
        result = CsvConverter.csv_to_dict(csv_bytes)
        assert result["blob"] == big

    def test_old_64mb_ceiling_would_have_rejected_65mb(self):
        # Proves the bump matters: under the previous 64 MB limit, reading a
        # 65 MB cell raises. Restore the real (raised) limit afterward.
        big = "y" * (65 * 1024 * 1024)
        csv_bytes, _, _ = CsvConverter.dict_to_csv({"blob": big})
        original = csv.field_size_limit()
        try:
            csv.field_size_limit(64 * 1024 * 1024)
            with pytest.raises(csv.Error):
                CsvConverter.csv_to_dict(csv_bytes)
        finally:
            csv.field_size_limit(original)
