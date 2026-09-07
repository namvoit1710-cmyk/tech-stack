"""Compound reference rules (Filter Rules), child nesting, and source-row identity.

A Filter Rule constrains a PAIR — "is this supplier valid for that purchasing
organization?" — so it cannot be expressed as two independent column checks.
Nesting is the reverse direction: many child rows collapsing back into one array
under their parent, which is what a deep-insert persistence layer expects.
"""

import polars as pl
import pytest

from app.layer1_domain.entities.validation import ValidationRule
from app.layer4_frameworks.providers.formats.csv_reader import CsvSourceReader
from app.layer4_frameworks.providers.formats.parquet_reader import ParquetSourceReader
from app.layer4_frameworks.providers.transformation.polars_schema_transformer_provider import (
    PolarsSchemaTransformerProvider,
)
from app.layer4_frameworks.providers.validation.polars_validator_provider import (
    PolarsValidatorProvider,
)


class _Logger:
    def __init__(self):
        self.warnings = []

    def info(self, *args, **kwargs): pass
    def debug(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass

    def warning(self, message, *args, **kwargs):
        self.warnings.append(str(message))


class _LocalStorage:
    def __init__(self):
        self._readers = {"csv": CsvSourceReader(), "parquet": ParquetSourceReader()}

    def download_and_read(self, file_path, file_format, version_id=None, sheet_names=None,
                          merge_sheets=False, add_sheet_name_column=False, header_row=None):
        return self._readers[file_format.lstrip(".").lower()].read(
            file_path, sheet_names=sheet_names, merge_sheets=merge_sheets,
            add_sheet_name_column=add_sheet_name_column, header_row=header_row)


@pytest.fixture
def validator():
    logger = _Logger()
    return PolarsValidatorProvider(logger=logger, storage=_LocalStorage()), logger


@pytest.fixture
def transformer():
    logger = _Logger()
    return PolarsSchemaTransformerProvider(logger=logger, storage=_LocalStorage()), logger


def _rule(**kwargs):
    kwargs.setdefault("type", "reference_lookup")
    kwargs.setdefault("error_message", "Not a valid combination")
    return ValidationRule(**kwargs)


def _write(df, tmp_path, name, fmt="csv"):
    path = str(tmp_path / f"{name}.{fmt}")
    df.write_csv(path) if fmt == "csv" else df.write_parquet(path)
    return path


# ------------------------------------------------------- Filter Rules (compound)


@pytest.fixture
def country_region(tmp_path):
    """Filter 4: GENERALDATA.countryOfOrigin -> regionOfOrigin (property countryID)."""
    return _write(
        pl.DataFrame({
            "countryOfOrigin": ["AU", "AU", "DE", "DE"],
            "regionOfOrigin": ["NSW", "VIC", "NRW", "BY"],
        }),
        tmp_path, "country-region",
    )


def test_a_valid_pair_passes_and_an_invalid_pair_is_flagged(validator, country_region):
    provider, _ = validator
    df = pl.DataFrame({
        "countryOfOrigin": ["AU", "DE", "AU"],
        "regionOfOrigin": ["NSW", "NRW", "NRW"],  # third row: both values exist, pair does not
    })
    rule = _rule(rule_name="FILTER_COUNTRY_REGION", params={
        "reference_source": country_region, "reference_format": "csv",
        "source_columns": ["countryOfOrigin", "regionOfOrigin"],
        "violate_when": "not_in_set",
    })

    exprs, mapping = provider._process_reference_rules([rule], df)
    out = df.with_columns(exprs)
    flagged = [row[list(mapping)[0]] for row in out.to_dicts()]

    assert [f is not None for f in flagged] == [False, False, True]
    assert flagged[2]["field"] == "regionOfOrigin"
    assert flagged[2]["rule"] == "FILTER_COUNTRY_REGION"
    assert flagged[2]["value"] == "NRW"


def test_this_is_why_single_column_mode_is_not_enough(validator, country_region):
    """Checked independently, ('AU','NRW') passes: both values exist somewhere."""
    provider, _ = validator
    df = pl.DataFrame({"countryOfOrigin": ["AU"], "regionOfOrigin": ["NRW"]})

    single = _rule(rule_name="SINGLE", params={
        "reference_source": country_region, "reference_format": "csv",
        "columns": ["regionOfOrigin"], "key_column": "regionOfOrigin",
        "violate_when": "not_in_set",
    })
    exprs, mapping = provider._process_reference_rules([single], df)
    assert df.with_columns(exprs).to_dicts()[0][list(mapping)[0]] is None  # missed

    compound = _rule(rule_name="COMPOUND", params={
        "reference_source": country_region, "reference_format": "csv",
        "source_columns": ["countryOfOrigin", "regionOfOrigin"],
        "violate_when": "not_in_set",
    })
    exprs, mapping = provider._process_reference_rules([compound], df)
    assert df.with_columns(exprs).to_dicts()[0][list(mapping)[0]] is not None  # caught


def test_case_and_padding_do_not_change_the_verdict(validator, country_region):
    provider, _ = validator
    df = pl.DataFrame({"countryOfOrigin": ["  au "], "regionOfOrigin": ["nsw"]})
    rule = _rule(rule_name="F", params={
        "reference_source": country_region, "reference_format": "csv",
        "source_columns": ["countryOfOrigin", "regionOfOrigin"],
        "violate_when": "not_in_set",
    })

    exprs, mapping = provider._process_reference_rules([rule], df)
    assert df.with_columns(exprs).to_dicts()[0][list(mapping)[0]] is None


def test_a_separator_in_the_data_cannot_forge_a_key(validator, tmp_path):
    """("A|B","C") must not satisfy a key built from ("A","B|C")."""
    provider, _ = validator
    reference = _write(pl.DataFrame({"left": ["A"], "right": ["B|C"]}), tmp_path, "sep")
    df = pl.DataFrame({"left": ["A|B"], "right": ["C"]})
    rule = _rule(rule_name="SEP", params={
        "reference_source": reference, "reference_format": "csv",
        "source_columns": ["left", "right"], "violate_when": "not_in_set",
    })

    exprs, mapping = provider._process_reference_rules([rule], df)
    assert df.with_columns(exprs).to_dicts()[0][list(mapping)[0]] is not None


def test_an_incomplete_pair_is_not_reported_as_an_invalid_pair(validator, country_region):
    """A blank target is `required`'s complaint, not the filter rule's."""
    provider, _ = validator
    df = pl.DataFrame({"countryOfOrigin": ["AU"], "regionOfOrigin": [None]})
    rule = _rule(rule_name="F", params={
        "reference_source": country_region, "reference_format": "csv",
        "source_columns": ["countryOfOrigin", "regionOfOrigin"],
        "violate_when": "not_in_set",
    })

    exprs, mapping = provider._process_reference_rules([rule], df)
    assert df.with_columns(exprs).to_dicts()[0][list(mapping)[0]] is None


def test_the_reported_field_can_be_pinned(validator, country_region):
    provider, _ = validator
    df = pl.DataFrame({"countryOfOrigin": ["AU"], "regionOfOrigin": ["NRW"]})
    rule = _rule(rule_name="F", params={
        "reference_source": country_region, "reference_format": "csv",
        "source_columns": ["countryOfOrigin", "regionOfOrigin"],
        "field": "countryOfOrigin", "violate_when": "not_in_set",
    })

    exprs, mapping = provider._process_reference_rules([rule], df)
    assert df.with_columns(exprs).to_dicts()[0][list(mapping)[0]]["field"] == "countryOfOrigin"


def test_reference_columns_may_be_named_differently(validator, tmp_path):
    provider, _ = validator
    reference = _write(
        pl.DataFrame({"purchasingOrganization": ["1000"], "supplierId": ["V1"]}),
        tmp_path, "purchorg-supplier")
    df = pl.DataFrame({"purchasingOrganization": ["1000", "1000"], "supplier": ["V1", "V9"]})
    rule = _rule(rule_name="FILTER_PURCHORG_SUPPLIER", params={
        "reference_source": reference, "reference_format": "csv",
        "source_columns": ["purchasingOrganization", "supplier"],
        "key_columns": ["purchasingOrganization", "supplierId"],
        "violate_when": "not_in_set",
    })

    exprs, mapping = provider._process_reference_rules([rule], df)
    flagged = [row[list(mapping)[0]] for row in df.with_columns(exprs).to_dicts()]
    assert [f is not None for f in flagged] == [False, True]


def test_a_rule_naming_a_missing_column_is_skipped_not_fatal(validator, country_region):
    """Previously this raised mid-run and took every other rule down with it."""
    provider, logger = validator
    df = pl.DataFrame({"countryOfOrigin": ["AU"]})
    rule = _rule(rule_name="F", params={
        "reference_source": country_region, "reference_format": "csv",
        "source_columns": ["countryOfOrigin", "regionOfOrigin"],
    })

    exprs, mapping = provider._process_reference_rules([rule], df)
    assert exprs == [] and mapping == {}
    assert any("regionOfOrigin" in message for message in logger.warnings)


def test_single_column_mode_still_works(validator, tmp_path):
    provider, _ = validator
    reference = _write(pl.DataFrame({"key": ["banned"]}), tmp_path, "blocklist")
    df = pl.DataFrame({"articleType": ["HAWA", "BANNED"]})
    rule = _rule(rule_name="BLOCKED", params={
        "reference_source": reference, "reference_format": "csv",
        "columns": ["articleType"], "violate_when": "in_set",
    })

    exprs, mapping = provider._process_reference_rules([rule], df)
    flagged = [row[list(mapping)[0]] for row in df.with_columns(exprs).to_dicts()]
    assert [f is not None for f in flagged] == [False, True]


def test_all_six_art41_filter_rules_compile_and_run(validator, tmp_path):
    """The six Filter Rules the template assigns, as one validation pass."""
    provider, _ = validator
    pairs = [
        ("charc", "charcValue", [("COLOR", "RED")]),
        ("class", "charc", [("CL1", "COLOR")]),
        ("classType", "class", [("001", "CL1")]),
        ("countryOfOrigin", "regionOfOrigin", [("AU", "NSW")]),
        ("purchasingOrganization", "supplier", [("1000", "V1")]),
        ("salesPurchasingOrganization", "salesSupplier", [("1000", "V1")]),
    ]
    df = pl.DataFrame({
        # First row valid everywhere, second row invalid everywhere.
        "charc": ["COLOR", "COLOR"], "charcValue": ["RED", "PURPLE"],
        "class": ["CL1", "CL9"], "classType": ["001", "001"],
        "countryOfOrigin": ["AU", "AU"], "regionOfOrigin": ["NSW", "NRW"],
        "purchasingOrganization": ["1000", "1000"], "supplier": ["V1", "V9"],
        "salesPurchasingOrganization": ["1000", "1000"], "salesSupplier": ["V1", "V9"],
    })

    rules = []
    for source, target, rows in pairs:
        reference = _write(
            pl.DataFrame({source: [r[0] for r in rows], target: [r[1] for r in rows]}),
            tmp_path, f"ref-{source}-{target}")
        rules.append(_rule(
            rule_name=f"FILTER_{source}_{target}".upper(),
            error_message=f"{target} is not valid for the selected {source}",
            params={"reference_source": reference, "reference_format": "csv",
                    "source_columns": [source, target], "violate_when": "not_in_set"}))

    exprs, mapping = provider._process_reference_rules(rules, df)
    assert len(exprs) == 6

    out = df.with_columns(exprs)
    per_row = [
        sum(1 for alias in mapping if row[alias] is not None) for row in out.to_dicts()
    ]
    assert per_row == [0, 6]


# ------------------------------------------------------------------- nesting


def test_child_rows_collapse_into_one_array_per_parent(transformer, tmp_path):
    """Five sales-tax rows for item 1 must become one five-element array."""
    provider, _ = transformer
    child = _write(pl.DataFrame({
        "itemID": [1, 1, 2],
        "country": ["AU", "DE", "AU"],
        "taxCategory": ["ATX1", "MWST", "ATX1"],
    }), tmp_path, "salestax")
    parent = pl.DataFrame({"itemID": [1, 2], "article": ["40059", "42402"]})

    out = provider._apply_rules(parent, [{
        "type": "nest_children",
        "params": {"on": ["itemID"], "children": [
            {"file_id": child, "as": "to_SalesTax"}]},
    }])

    assert out.height == 2  # still one row per parent
    rows = out.to_dicts()
    assert len(rows[0]["to_SalesTax"]) == 2
    assert len(rows[1]["to_SalesTax"]) == 1
    assert rows[0]["to_SalesTax"][0]["country"] == "AU"
    # The key is not repeated inside each child record by default.
    assert "itemID" not in rows[0]["to_SalesTax"][0]


def test_dummy_key_records_the_child_position(transformer, tmp_path):
    """MDGDummyKey is the address that survives flattening: to_X[0], to_X[1]."""
    provider, _ = transformer
    child = _write(pl.DataFrame({"itemID": [1, 1], "plant": ["RF11", "RFDC"]}),
                   tmp_path, "plant")
    parent = pl.DataFrame({"itemID": [1]})

    out = provider._apply_rules(parent, [{
        "type": "nest_children",
        "params": {"on": ["itemID"], "children": [
            {"file_id": child, "as": "to_ArticlePlant", "dummy_key": "MDGDummyKey"}]},
    }])

    keys = [record["MDGDummyKey"] for record in out.to_dicts()[0]["to_ArticlePlant"]]
    assert sorted(keys) == ["to_ArticlePlant[0]", "to_ArticlePlant[1]"]


def test_several_children_nest_side_by_side(transformer, tmp_path):
    provider, _ = transformer
    plant = _write(pl.DataFrame({"itemID": [1], "plant": ["RF11"]}), tmp_path, "p")
    uom = _write(pl.DataFrame({"itemID": [1, 1], "alternativeUnit": ["EA", "CS"]}), tmp_path, "u")
    parent = pl.DataFrame({"itemID": [1]})

    out = provider._apply_rules(parent, [{
        "type": "nest_children",
        "params": {"on": ["itemID"], "children": [
            {"file_id": plant, "as": "to_ArticlePlant"},
            {"file_id": uom, "as": "to_UOM"}]},
    }])

    row = out.to_dicts()[0]
    assert len(row["to_ArticlePlant"]) == 1 and len(row["to_UOM"]) == 2


def test_a_parent_with_no_children_survives_the_nesting(transformer, tmp_path):
    provider, _ = transformer
    child = _write(pl.DataFrame({"itemID": [1], "plant": ["RF11"]}), tmp_path, "p")
    parent = pl.DataFrame({"itemID": [1, 2]})

    out = provider._apply_rules(parent, [{
        "type": "nest_children",
        "params": {"on": ["itemID"], "children": [{"file_id": child, "as": "to_Plant"}]},
    }])

    assert out.height == 2
    assert out.to_dicts()[1]["to_Plant"] is None


def test_nested_output_serializes_to_a_deep_json_object(transformer, tmp_path):
    """The end of the round trip: flat tables back to the deep shape."""
    import json

    provider, _ = transformer
    plant = _write(pl.DataFrame({"itemID": [1], "plant": ["1000"]}), tmp_path, "plant")
    parent = pl.DataFrame({"itemID": [1], "article": ["A100"], "baseUnit": ["EA"]})

    out = provider._apply_rules(parent, [{
        "type": "nest_children",
        "params": {"on": ["itemID"], "children": [
            {"file_id": plant, "as": "to_ArticlePlant", "dummy_key": "MDGDummyKey"}]},
    }])
    path = str(tmp_path / "deep.json")
    out.write_json(path)

    deep = json.load(open(path, encoding="utf-8"))[0]
    assert deep["article"] == "A100"
    assert deep["to_ArticlePlant"][0]["plant"] == "1000"
    assert deep["to_ArticlePlant"][0]["MDGDummyKey"] == "to_ArticlePlant[0]"


def test_nesting_aligns_mismatched_key_dtypes(transformer, tmp_path):
    provider, _ = transformer
    child = _write(pl.DataFrame({"itemID": ["1"], "plant": ["RF11"]}), tmp_path, "c")
    parent = pl.DataFrame({"itemID": [1]})

    out = provider._apply_rules(parent, [{
        "type": "nest_children",
        "params": {"on": ["itemID"], "children": [{"file_id": child, "as": "to_Plant"}]},
    }])

    assert out.to_dicts()[0]["to_Plant"] is not None


def test_nesting_skips_a_child_missing_the_key(transformer, tmp_path):
    provider, logger = transformer
    child = _write(pl.DataFrame({"plant": ["RF11"]}), tmp_path, "c")
    parent = pl.DataFrame({"itemID": [1]})

    out = provider._apply_rules(parent, [{
        "type": "nest_children",
        "params": {"on": ["itemID"], "children": [{"file_id": child, "as": "to_Plant"}]},
    }])

    assert out.columns == ["itemID"]
    assert any("missing key column" in message for message in logger.warnings)


# ------------------------------------------------------------- source row


def test_add_row_index_gives_every_row_a_source_address(transformer):
    provider, _ = transformer
    df = pl.DataFrame({"itemID": [1, 1, 2]})

    out = provider._apply_rules(df, [{"type": "add_row_index", "params": {}}])

    # ItemID alone cannot say WHICH of item 1's rows is bad; the index can.
    assert out["__source_row"].to_list() == [1, 2, 3]


def test_add_row_index_name_and_offset_are_configurable(transformer):
    provider, _ = transformer
    df = pl.DataFrame({"itemID": [1, 2]})

    out = provider._apply_rules(
        df, [{"type": "add_row_index", "params": {"name": "excelRow", "offset": 4}}])

    assert out["excelRow"].to_list() == [4, 5]


def test_add_row_index_does_not_clobber_an_existing_column(transformer):
    provider, _ = transformer
    df = pl.DataFrame({"__source_row": [9], "itemID": [1]})

    out = provider._apply_rules(df, [{"type": "add_row_index", "params": {}}])

    assert out["__source_row"].to_list() == [9]
