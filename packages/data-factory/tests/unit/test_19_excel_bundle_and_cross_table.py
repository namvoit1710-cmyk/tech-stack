"""Multi-table workbook ingestion: header_row, typed casts, cross-table rules, bundle.

The fixture is built to the shape of an SMDG mass-upload template: row 1 names the
logical table (a second table starts partway across the sheet), row 2 names the
section, row 3 holds ``Label (Type)`` field headers, and data starts on row 4.
Child sheets repeat the ItemID to express 1:N.
"""

import os
import tempfile

import polars as pl
import pytest

from app.layer4_frameworks.providers.formats.excel_reader import ExcelSourceReader
from app.layer4_frameworks.providers.formats.csv_reader import CsvSourceReader
from app.layer4_frameworks.providers.transformation.polars_schema_transformer_provider import (
    PolarsSchemaTransformerProvider,
)


class _Logger:
    def info(self, *args, **kwargs): pass
    def debug(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass

    def __init__(self):
        self.warnings = []

    def warning(self, message, *args, **kwargs):
        self.warnings.append(str(message))


class _LocalStorage:
    """Reads back whatever path it is handed, as the real provider does for local files."""

    def __init__(self, readers):
        self._readers = readers

    def download_and_read(
        self,
        file_path,
        file_format,
        version_id=None,
        sheet_names=None,
        merge_sheets=False,
        add_sheet_name_column=False,
        header_row=None,
    ):
        return self._readers[file_format.lstrip(".").lower()].read(
            file_path,
            sheet_names=sheet_names,
            merge_sheets=merge_sheets,
            add_sheet_name_column=add_sheet_name_column,
            header_row=header_row,
        )


@pytest.fixture
def workbook(tmp_path):
    """A two-sheet template with banner rows and a side-by-side second table."""
    import xlsxwriter

    path = str(tmp_path / "ART41.00-sample.xlsx")
    book = xlsxwriter.Workbook(path)

    parent = book.add_worksheet("ArticleMaster")
    # Row 1: the logical table names. Columns 5+ are a second table on the same sheet.
    parent.write_row(0, 0, ["ArticleMaster", "", "", "", "", "ARTMasterPurValue"])
    parent.write_row(1, 0, ["Key Fields", "", "", "General data", "", "Purchasing Value"])
    parent.write_row(
        2,
        0,
        [
            "ItemID (Integer)",
            "Article (String)",
            "variant (String)",
            "Article Group (String)",
            "Base Unit (String)",
            "Overdelivery Tolerance (Decimal)",
        ],
    )
    parent.write_row(3, 0, [1, "40059", "40059", "FOOD", "EA", "0"])
    parent.write_row(4, 0, [2, "42402", "42402", "NONFOOD", "EA", "5"])
    parent.write_row(5, 0, [3, "42582", "42582", "FOOD", "PC", "0"])

    child = book.add_worksheet("ARTMasterPurchasing")
    child.write_row(0, 0, ["ARTMasterPurchasing"])
    child.write_row(1, 0, ["Key Fields"])
    child.write_row(
        2,
        0,
        [
            "ItemID (Integer)",
            "Article (String)",
            "Purchasing Info Record (String)",
            "Valid From (yyyy-MM-dd)",
        ],
    )
    # ItemID 1 twice: 1:N, the shape a naive one-row-per-item reader would lose.
    child.write_row(3, 0, [1, "40059", "", "2025-06-23"])
    child.write_row(4, 0, [1, "40059", "", "2025-07-01"])
    child.write_row(5, 0, [2, "42402", "XX", ""])
    child.write_row(6, 0, [3, "42582", "", "2025-09-04"])

    book.close()
    return path


@pytest.fixture
def transformer():
    readers = {"xlsx": ExcelSourceReader(), "xls": ExcelSourceReader(), "csv": CsvSourceReader()}
    logger = _Logger()
    return PolarsSchemaTransformerProvider(logger=logger, storage=_LocalStorage(readers)), logger


# ---------------------------------------------------------------- header_row


def test_default_read_cannot_see_the_real_header(workbook):
    """Without header_row the banner row wins and the fields are unreadable."""
    df = ExcelSourceReader().read(workbook, sheet_names=["ArticleMaster"])

    assert df.columns[0] == "ArticleMaster"
    assert any(c.startswith("__UNNAMED__") for c in df.columns)
    # The two banner rows are counted as data.
    assert df.height == 5


def test_header_row_pins_the_field_header(workbook):
    df = ExcelSourceReader().read(
        workbook, sheet_names=["ArticleMaster"], header_row=2
    )

    assert df.columns[:3] == ["ItemID (Integer)", "Article (String)", "variant (String)"]
    assert df.height == 3


def test_header_row_survives_multi_sheet_merge(workbook):
    df = ExcelSourceReader().read(workbook, merge_sheets=True, header_row=2)

    assert "ItemID (Integer)" in df.columns
    assert df.height == 7  # 3 parent rows + 4 child rows


def test_negative_header_row_is_rejected(workbook):
    with pytest.raises(ValueError, match="0-based row index"):
        ExcelSourceReader().read(workbook, sheet_names=["ArticleMaster"], header_row=-1)


def test_csv_reader_honours_header_row(tmp_path):
    path = str(tmp_path / "banner.csv")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("ArticleMaster,,\nKey Fields,,\nitemID,article,baseUnit\n1,40059,EA\n")

    df = CsvSourceReader().read(path, header_row=2)

    assert df.columns == ["itemID", "article", "baseUnit"]
    assert df.height == 1


def test_flat_readers_ignore_header_row(tmp_path):
    """json/parquet accept the kwarg and are unaffected, as with the sheet kwargs."""
    from app.layer4_frameworks.providers.formats.json_reader import JsonSourceReader
    from app.layer4_frameworks.providers.formats.parquet_reader import ParquetSourceReader

    json_path = str(tmp_path / "d.json")
    parquet_path = str(tmp_path / "d.parquet")
    frame = pl.DataFrame({"itemID": [1], "article": ["40059"]})
    frame.write_json(json_path)
    frame.write_parquet(parquet_path)

    assert JsonSourceReader().read(json_path, header_row=2).columns == ["itemID", "article"]
    assert ParquetSourceReader().read(parquet_path, header_row=2).columns == ["itemID", "article"]


# ---------------------------------------------------------------- typed casts


def test_cast_accepts_a_column_list(workbook, transformer):
    provider, _ = transformer
    df = ExcelSourceReader().read(workbook, sheet_names=["ArticleMaster"], header_row=2)
    df = df.rename({"ItemID (Integer)": "itemID", "Base Unit (String)": "baseUnit"})

    out = provider._apply_rules(
        df, [{"type": "cast", "params": {"columns": ["itemID", "baseUnit"], "dtype": "string"}}]
    )

    assert out.schema["itemID"] == pl.Utf8
    assert out.schema["baseUnit"] == pl.Utf8


def test_decimal_dtype_is_numeric_not_text(workbook, transformer):
    """'Decimal' is what the template annotates money/quantity with."""
    provider, _ = transformer
    df = ExcelSourceReader().read(workbook, sheet_names=["ArticleMaster"], header_row=2)
    df = df.rename({"Overdelivery Tolerance (Decimal)": "overdeliveryTolerance"})
    df = df.with_columns(pl.col("overdeliveryTolerance").cast(pl.Utf8))

    out = provider._apply_rules(
        df,
        [{"type": "cast", "params": {"column": "overdeliveryTolerance", "dtype": "decimal"}}],
    )

    assert out.schema["overdeliveryTolerance"] == pl.Float64


def test_text_dates_parse_to_date(workbook, transformer):
    provider, _ = transformer
    df = ExcelSourceReader().read(workbook, sheet_names=["ARTMasterPurchasing"], header_row=2)
    df = df.rename({"Valid From (yyyy-MM-dd)": "validFrom"})
    df = df.with_columns(pl.col("validFrom").cast(pl.Utf8))

    out = provider._apply_rules(
        df,
        [
            {
                "type": "cast",
                "params": {"column": "validFrom", "dtype": "date", "strict": False},
            }
        ],
    )

    assert out.schema["validFrom"] == pl.Date
    assert out["validFrom"].to_list()[0].isoformat() == "2025-06-23"
    # The blank cell is "no date", not a parse failure.
    assert out["validFrom"].to_list()[2] is None


def test_unknown_dtype_warns_instead_of_silently_becoming_text(transformer):
    provider, logger = transformer
    df = pl.DataFrame({"quantity": ["1"]})

    out = provider._apply_rules(
        df, [{"type": "cast", "params": {"column": "quantity", "dtype": "int32ish"}}]
    )

    assert out.schema["quantity"] == pl.Utf8
    assert any("int32ish" in message for message in logger.warnings)


# ---------------------------------------------------------------- cross-table


def _materialize(df, tmp_path, name):
    path = str(tmp_path / f"{name}.csv")
    df.write_csv(path)
    return path


def test_join_reference_pulls_a_parent_field_onto_child_rows(workbook, transformer, tmp_path):
    provider, _ = transformer
    parent = ExcelSourceReader().read(
        workbook, sheet_names=["ArticleMaster"], header_row=2
    ).rename({"ItemID (Integer)": "itemID", "Article Group (String)": "articleGroup"})
    parent_path = _materialize(parent.select(["itemID", "articleGroup"]), tmp_path, "parent")

    child = ExcelSourceReader().read(
        workbook, sheet_names=["ARTMasterPurchasing"], header_row=2
    ).rename({"ItemID (Integer)": "itemID"})

    out = provider._apply_rules(
        child,
        [
            {
                "type": "join_reference",
                "params": {
                    "file_id": parent_path,
                    "file_format": "csv",
                    "on": ["itemID"],
                    "columns": ["articleGroup"],
                    "prefix": "_ref_",
                },
            }
        ],
    )

    assert "_ref_articleGroup" in out.columns
    # Both child rows for ItemID 1 get the parent's value: the 1:N fan-out holds.
    assert out.filter(pl.col("itemID") == 1)["_ref_articleGroup"].to_list() == ["FOOD", "FOOD"]
    assert out.height == child.height


def test_join_reference_aligns_mismatched_key_dtypes(transformer, tmp_path):
    """i64 on one side, text on the other, must still match."""
    provider, _ = transformer
    reference = pl.DataFrame({"itemID": ["1", "2"], "articleGroup": ["FOOD", "NONFOOD"]})
    path = _materialize(reference, tmp_path, "ref-text-keys")
    child = pl.DataFrame({"itemID": [1, 2], "plant": ["RF11", "RF11"]})

    out = provider._apply_rules(
        child,
        [
            {
                "type": "join_reference",
                "params": {"file_id": path, "on": ["itemID"], "columns": ["articleGroup"]},
            }
        ],
    )

    assert out["articleGroup"].to_list() == ["FOOD", "NONFOOD"]


def test_join_reference_skips_cleanly_when_the_key_is_absent(transformer, tmp_path):
    provider, logger = transformer
    path = _materialize(pl.DataFrame({"itemID": [1], "articleGroup": ["FOOD"]}), tmp_path, "r")
    child = pl.DataFrame({"plant": ["RF11"]})

    out = provider._apply_rules(
        child,
        [{"type": "join_reference", "params": {"file_id": path, "on": ["itemID"]}}],
    )

    assert out.columns == ["plant"]
    assert any("not on the frame" in message for message in logger.warnings)


def test_derive_applies_the_artm_food_rule(transformer):
    """ARTM_FOOD: articleGroup == FOOD derives seven targets. No eval involved."""
    provider, _ = transformer
    df = pl.DataFrame(
        {
            "itemID": [1, 2, 3],
            "articleGroup": ["FOOD", "NONFOOD", "FOOD"],
            "articleCategory": ["", "", ""],
        }
    )

    out = provider._apply_rules(
        df,
        [
            {
                "type": "derive",
                "params": {
                    "when": {"column": "articleGroup", "operator": "EQUAL", "value": "FOOD"},
                    "then": [
                        {"column": "articleCategory", "value": "01"},
                        {"column": "container", "value": "01"},
                        {"column": "temperature", "value": "01"},
                    ],
                },
            }
        ],
    )

    assert out["articleCategory"].to_list() == ["01", "", "01"]
    # A derived column the sheet does not carry is created, null where untouched.
    assert out["container"].to_list() == ["01", None, "01"]
    assert out["temperature"].to_list() == ["01", None, "01"]


def test_derive_preserves_leading_zeros_in_the_condition(transformer):
    provider, _ = transformer
    df = pl.DataFrame({"discountInKind": ["0001", "1"], "target": ["", ""]})

    out = provider._apply_rules(
        df,
        [
            {
                "type": "derive",
                "params": {
                    "when": {"column": "discountInKind", "value": "0001"},
                    "then": [{"column": "target", "value": "hit"}],
                },
            }
        ],
    )

    assert out["target"].to_list() == ["hit", ""]


def test_derive_only_when_empty_does_not_overwrite(transformer):
    provider, _ = transformer
    df = pl.DataFrame({"articleGroup": ["FOOD", "FOOD"], "container": ["99", ""]})

    out = provider._apply_rules(
        df,
        [
            {
                "type": "derive",
                "params": {
                    "when": {"column": "articleGroup", "value": "FOOD"},
                    "only_when_empty": True,
                    "then": [{"column": "container", "value": "01"}],
                },
            }
        ],
    )

    assert out["container"].to_list() == ["99", "01"]


@pytest.mark.parametrize(
    "operator,value,expected",
    [
        ("NOT_EQUAL", "FOOD", [False, True, False]),
        ("IN", ["FOOD", "DRINK"], [True, False, True]),
        ("NOT_IN", ["FOOD"], [False, True, False]),
        # "FOOD" is a substring of "NONFOOD", so a discriminating needle must
        # come from the prefix.
        ("CONTAINS", "NON", [False, True, False]),
        ("STARTS_WITH", "NON", [False, True, False]),
    ],
)
def test_derive_operators(transformer, operator, value, expected):
    provider, _ = transformer
    df = pl.DataFrame({"articleGroup": ["FOOD", "NONFOOD", "FOOD"], "hit": ["", "", ""]})

    out = provider._apply_rules(
        df,
        [
            {
                "type": "derive",
                "params": {
                    "when": {"column": "articleGroup", "operator": operator, "value": value},
                    "then": [{"column": "hit", "value": "Y"}],
                },
            }
        ],
    )

    assert [cell == "Y" for cell in out["hit"].to_list()] == expected


def test_derive_numeric_operators_compare_as_numbers(transformer):
    provider, _ = transformer
    df = pl.DataFrame({"tolerance": ["9", "10", "100"], "hit": ["", "", ""]})

    out = provider._apply_rules(
        df,
        [
            {
                "type": "derive",
                "params": {
                    "when": {"column": "tolerance", "operator": "GT", "value": 10},
                    "then": [{"column": "hit", "value": "Y"}],
                },
            }
        ],
    )

    # Text ordering would put "9" above "100"; numeric comparison must not.
    assert out["hit"].to_list() == ["", "", "Y"]


def test_derive_combines_conditions_with_all_and_any(transformer):
    provider, _ = transformer
    df = pl.DataFrame(
        {"articleGroup": ["FOOD", "FOOD", "NONFOOD"], "plant": ["RF11", "RFDC", "RF11"], "hit": ["", "", ""]}
    )
    rule = {
        "type": "derive",
        "params": {
            "when": {
                "match": "ALL",
                "conditions": [
                    {"column": "articleGroup", "value": "FOOD"},
                    {"column": "plant", "value": "RF11"},
                ],
            },
            "then": [{"column": "hit", "value": "Y"}],
        },
    }

    assert provider._apply_rules(df, [rule])["hit"].to_list() == ["Y", "", ""]

    rule["params"]["when"]["match"] = "ANY"
    assert provider._apply_rules(df, [rule])["hit"].to_list() == ["Y", "Y", "Y"]


def test_derive_skips_when_the_condition_column_is_missing(transformer):
    provider, logger = transformer
    df = pl.DataFrame({"hit": [""]})

    out = provider._apply_rules(
        df,
        [
            {
                "type": "derive",
                "params": {
                    "when": {"column": "articleGroup", "value": "FOOD"},
                    "then": [{"column": "hit", "value": "Y"}],
                },
            }
        ],
    )

    assert out["hit"].to_list() == [""]
    assert any("articleGroup" in message for message in logger.warnings)


def test_cross_table_derivation_end_to_end(workbook, transformer, tmp_path):
    """The full ARTM_FOOD shape: condition on the parent, target on the child."""
    provider, _ = transformer
    parent = ExcelSourceReader().read(
        workbook, sheet_names=["ArticleMaster"], header_row=2
    ).rename({"ItemID (Integer)": "itemID", "Article Group (String)": "articleGroup"})
    parent_path = _materialize(parent.select(["itemID", "articleGroup"]), tmp_path, "parent")

    child = ExcelSourceReader().read(
        workbook, sheet_names=["ARTMasterPurchasing"], header_row=2
    ).rename(
        {
            "ItemID (Integer)": "itemID",
            "Purchasing Info Record (String)": "purchasingInfoRecord",
        }
    )

    out = provider._apply_rules(
        child,
        [
            {
                "type": "join_reference",
                "params": {
                    "file_id": parent_path,
                    "on": ["itemID"],
                    "columns": ["articleGroup"],
                    "prefix": "_ref_",
                },
            },
            {
                "type": "derive",
                "params": {
                    "when": {"column": "_ref_articleGroup", "value": "FOOD"},
                    "then": [
                        {"column": "purchasingInfoRecord", "value": "PC"},
                        {"column": "purchasingInfoRecordCategory", "value": "4"},
                    ],
                },
            },
            {"type": "drop_columns", "params": {"columns": ["_ref_articleGroup"]}},
        ],
    )

    assert "_ref_articleGroup" not in out.columns
    # ItemID 1 and 3 are FOOD (1 has two rows); ItemID 2 is untouched.
    assert out["purchasingInfoRecord"].to_list() == ["PC", "PC", "XX", "PC"]
    assert out["purchasingInfoRecordCategory"].to_list() == ["4", "4", None, "4"]
