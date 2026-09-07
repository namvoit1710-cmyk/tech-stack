"""Unit tests for the injectable source-format readers + registry (DI refactor).

Covers the point-1 goal: the storage provider dispatches read/write to a
per-format ``ISourceReader`` strategy, and a new format is added by registering
a new reader — no change to business logic.
"""
import os
import tempfile

import polars as pl
import pytest

from app.layer4_frameworks.providers.formats.csv_reader import CsvSourceReader
from app.layer4_frameworks.providers.formats.json_reader import JsonSourceReader
from app.layer4_frameworks.providers.formats.parquet_reader import ParquetSourceReader
from app.layer4_frameworks.providers.formats.excel_reader import ExcelSourceReader
from app.layer4_frameworks.providers.formats.registry import (
    SourceReaderRegistry,
    default_source_reader_registry,
)


@pytest.mark.unit
class TestSourceReaderRegistry:
    def setup_method(self):
        self.registry = default_source_reader_registry()

    def test_default_registry_supports_shipped_formats(self):
        for fmt in ("csv", "json", "parquet", "xlsx", "xls"):
            assert self.registry.supports(fmt)

    def test_for_format_is_dot_and_case_insensitive(self):
        assert isinstance(self.registry.for_format(".CSV"), CsvSourceReader)
        assert isinstance(self.registry.for_format("XLSX"), ExcelSourceReader)

    def test_unsupported_format_raises_with_helpful_message(self):
        with pytest.raises(ValueError, match="Unsupported file format 'avro'"):
            self.registry.for_format("avro")

    def test_detect_reads_extension(self):
        assert self.registry.detect("/data/customers.CSV") == "csv"
        assert self.registry.detect("report.xlsx") == "xlsx"
        assert self.registry.detect("no_extension") == ""

    def test_new_format_added_without_touching_existing(self):
        """The whole point of the DI refactor: register a new reader -> supported."""

        class TsvSourceReader:
            formats = ("tsv",)

            def read(self, path, *, sheet_names=None, merge_sheets=False, add_sheet_name_column=False):
                return pl.read_csv(path, separator="\t")

            def write(self, df, path):
                df.write_csv(path, separator="\t")

        registry = SourceReaderRegistry([CsvSourceReader(), TsvSourceReader()])
        assert registry.supports("tsv")
        assert isinstance(registry.for_format("tsv"), TsvSourceReader)
        # existing csv still resolves
        assert isinstance(registry.for_format("csv"), CsvSourceReader)


@pytest.mark.unit
class TestCsvSourceReader:
    def test_reads_and_strips_columns_keeping_text(self, tmp_path):
        p = tmp_path / "d.csv"
        p.write_text(" id , name \n007,Alice\n", encoding="utf-8")
        df = CsvSourceReader().read(str(p))
        assert df.columns == ["id", "name"]
        # infer_schema_length=0 -> values kept as text (leading zero preserved)
        assert df["id"].to_list() == ["007"]

    def test_write_roundtrip(self, tmp_path):
        df = pl.DataFrame({"a": ["1"], "b": ["x"]})
        p = tmp_path / "out.csv"
        CsvSourceReader().write(df, str(p))
        assert os.path.exists(p)
        assert CsvSourceReader().read(str(p)).columns == ["a", "b"]


@pytest.mark.unit
class TestParquetSourceReader:
    def test_roundtrip(self, tmp_path):
        df = pl.DataFrame({"key": ["a", "b"]})
        p = tmp_path / "ref.parquet"
        ParquetSourceReader().write(df, str(p))
        out = ParquetSourceReader().read(str(p))
        assert out.columns == ["key"]
        assert out["key"].to_list() == ["a", "b"]


@pytest.mark.unit
class TestJsonSourceReader:
    def test_roundtrip(self, tmp_path):
        df = pl.DataFrame({"x": [1], "y": [2]})
        p = tmp_path / "d.json"
        JsonSourceReader().write(df, str(p))
        out = JsonSourceReader().read(str(p))
        assert set(out.columns) == {"x", "y"}
