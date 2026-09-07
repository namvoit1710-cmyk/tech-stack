"""The whole chain over a real-shaped workbook: read -> transform -> validate.

The unit tests around this one each hold a single seam still. This module runs
the seams together against a workbook built to the SMDG mass-upload shape, and
asserts *which rows* every rule family flags — not just how many.

The fixture is not invented. It mirrors the seeded-violation pattern of the real
ART41.00 500-row pair (`..._500_valid_rules.xlsx` / `..._500_invalid_rules.xlsx`),
where the two files are identical except for roughly twenty deliberately broken
cells. Counting rows is not enough to catch a rule that fires on the wrong ones,
so every assertion here names ItemIDs.

The same assertions run against the real workbooks when they are present; point
ART41_WORKBOOK_DIR at them, or they are skipped.
"""

import os

import polars as pl
import pytest

from app.layer1_domain.entities.validation import ValidationRule
from app.layer4_frameworks.providers.formats.csv_reader import CsvSourceReader
from app.layer4_frameworks.providers.formats.excel_reader import ExcelSourceReader
from app.layer4_frameworks.providers.transformation.polars_schema_transformer_provider import (
    PolarsSchemaTransformerProvider,
)
from app.layer4_frameworks.providers.validation.polars_validator_provider import (
    PolarsValidatorProvider,
)
from app.layer4_frameworks.providers.validation.rule_handlers import RuleFactory
from app.layer4_frameworks.providers.expressions.safe_expression import RuleExpressionError

ITEM = "ItemID (Integer)"
MERCH = "Merchandise Category (String)"
CATEGORY = "Article Category (String)"
BASE_UNIT = "Base Unit (String)"
TEMPERATURE = "Temperature (String)"
CONTAINER = "Container (String)"
DISCOUNT = "Disc. in kind (String)"
REGION = "Region Of Origin (String)"
COUNTRY = "Country Of Origin (String)"
PVK = "Purchasing value key (String)"
VALID_FROM = "Valid From (yyyy-MM-dd)"
PIR = "Purchasing Info Record (String)"
PIR_CATEGORY = "Purchasing Info Record Category (String)"
PRICE = "Effective Price (Decimal)"

#: The seeded violations, by ItemID. Straight from diffing the two real files.
LONG_BASE_UNIT = [7, 8]
BROKEN_DERIVATION = [7, 17]
BAD_REGION = [9]
BAD_PVK = [13]
BROKEN_CHILD_DERIVATION = [7, 17]

OVERLONG = "UNIT_VALUE_EXCEEDING_40_CHARACTERS_0001_ABC"


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
        self._readers = {"xlsx": ExcelSourceReader(), "csv": CsvSourceReader()}

    def download_and_read(self, file_path, file_format, version_id=None, sheet_names=None,
                          merge_sheets=False, add_sheet_name_column=False, header_row=None):
        return self._readers[file_format.lstrip(".").lower()].read(
            file_path, sheet_names=sheet_names, merge_sheets=merge_sheets,
            add_sheet_name_column=add_sheet_name_column, header_row=header_row)


# ------------------------------------------------------------------ fixtures

def _rows(broken: bool):
    """Twenty items. FOOD derives 01/01/01/0001; GROCERY derives 00/null.

    When `broken`, the same cells the real invalid workbook breaks are broken
    here, on the same ItemIDs.
    """
    parent, child = [], []
    for item in range(1, 21):
        food = item in (7, 8, 17)
        row = {
            ITEM: item,
            MERCH: "FOOD" if food else "GROCERY",
            CATEGORY: "01" if food else "00",
            BASE_UNIT: "EA",
            TEMPERATURE: "01" if food else None,
            CONTAINER: "01" if food else None,
            DISCOUNT: "0001" if food else None,
            COUNTRY: "AU",
            REGION: "23",
            PVK: "NORM",
            VALID_FROM: "2025-06-23",
        }
        if broken and item in BROKEN_DERIVATION:
            row.update({CATEGORY: "99", TEMPERATURE: "99",
                        CONTAINER: "99", DISCOUNT: "9999"})
        if broken and item in LONG_BASE_UNIT:
            row[BASE_UNIT] = OVERLONG
        if broken and item in BAD_REGION:
            row[REGION] = "INVALID_REGION"
        if broken and item in BAD_PVK:
            row[PVK] = "Z001"
        parent.append(row)

        c = {ITEM: item, PIR: "PC" if food else "", PIR_CATEGORY: "4" if food else "",
             PRICE: 8}
        if broken and item in BROKEN_CHILD_DERIVATION:
            c.update({PIR: "WRONG", PIR_CATEGORY: "9", PRICE: 9})
        child.append(c)
    return parent, child


def _write_workbook(path, broken: bool):
    import xlsxwriter

    parent_rows, child_rows = _rows(broken)
    book = xlsxwriter.Workbook(path)

    sheet = book.add_worksheet("ArticleMaster")
    columns = list(parent_rows[0])
    sheet.write_row(0, 0, ["ArticleMaster"])            # row 1: logical table
    sheet.write_row(1, 0, ["Key Fields", "", "General data"])  # row 2: section
    sheet.write_row(2, 0, columns)                       # row 3: the real header
    for index, row in enumerate(parent_rows):
        sheet.write_row(3 + index, 0, [row[c] for c in columns])

    sheet = book.add_worksheet("ARTMasterPurchasing")
    columns = list(child_rows[0])
    sheet.write_row(0, 0, ["ARTMasterPurchasing"])
    sheet.write_row(1, 0, ["Key Fields"])
    sheet.write_row(2, 0, columns)
    for index, row in enumerate(child_rows):
        sheet.write_row(3 + index, 0, [row[c] for c in columns])

    book.close()
    return path


@pytest.fixture
def clean_workbook(tmp_path):
    return _write_workbook(str(tmp_path / "ART41-clean.xlsx"), broken=False)


@pytest.fixture
def broken_workbook(tmp_path):
    return _write_workbook(str(tmp_path / "ART41-broken.xlsx"), broken=True)


@pytest.fixture
def validator():
    return PolarsValidatorProvider(logger=_Logger(), storage=_LocalStorage())


@pytest.fixture
def transformer():
    logger = _Logger()
    return PolarsSchemaTransformerProvider(logger=logger, storage=_LocalStorage()), logger


# ------------------------------------------------------------------- helpers

def _read(path, sheet="ArticleMaster"):
    return ExcelSourceReader().read(path, sheet_names=[sheet], header_row=2)


def _flagged_items(df, rule):
    """Run one rule and return the ItemIDs it flags, in order."""
    handler = RuleFactory().get_handler(rule.type)
    expressions = handler.parse_rule(rule, "err_0")
    out = df.with_columns(expressions)
    error_columns = [c for c in out.columns if c.startswith("err_0")]
    items = []
    for row in out.to_dicts():
        if any(row[c] is not None for c in error_columns):
            items.append(row[ITEM])
    return items


def _expression_rule(name, expression, columns):
    return ValidationRule(rule_name=name, type="expression",
                          error_message=f"{name} failed",
                          params={"columns": columns, "expression": expression})


# --------------------------------------------------------------- reading it

def test_the_banner_rows_are_not_data(clean_workbook):
    df = _read(clean_workbook)

    assert df.columns[0] == ITEM
    assert df.height == 20
    assert not any(c.startswith("__UNNAMED__") for c in df.columns)


def test_without_header_row_the_workbook_is_unreadable(clean_workbook):
    df = ExcelSourceReader().read(clean_workbook, sheet_names=["ArticleMaster"])

    assert df.columns[0] == "ArticleMaster"
    assert df.height == 22  # the two banner rows counted as data


def test_a_text_date_column_casts_to_a_real_date(clean_workbook, transformer):
    """A plain cast cannot parse text into a date; this is the str.to_date path."""
    provider, _ = transformer
    df = _read(clean_workbook)
    assert df[VALID_FROM].dtype == pl.Utf8

    out = provider._apply_rules(df, [  # noqa: SLF001 - the seam under test
        {"type": "cast", "params": {"columns": [VALID_FROM], "dtype": "date",
                                    "format": "%Y-%m-%d"}}
    ])

    assert out[VALID_FROM].dtype == pl.Date
    assert out[VALID_FROM][0].year == 2025


def test_the_derive_rule_fills_the_food_fields(clean_workbook, transformer):
    """`derive` is the declarative form of the same business rule the expression
    above only checks. Blank the derived fields, then let the rule restore them."""
    provider, _ = transformer
    df = _read(clean_workbook).with_columns(pl.lit(None).cast(pl.Utf8).alias(CATEGORY))

    out = provider._apply_rules(df, [  # noqa: SLF001
        {"type": "derive", "params": {
            "when": {"column": MERCH, "operator": "EQUAL", "value": "FOOD"},
            "then": [{"column": CATEGORY, "value": "01"}]}}
    ])

    food = out.filter(pl.col(MERCH) == "FOOD")
    grocery = out.filter(pl.col(MERCH) == "GROCERY")
    assert food[CATEGORY].unique().to_list() == ["01"]
    assert grocery[CATEGORY].unique().to_list() == [None]


# ------------------------------------------------------- rule family: length

def test_a_length_rule_flags_exactly_the_overlong_rows(broken_workbook, clean_workbook):
    rule = _expression_rule(
        "LENGTH_40",
        f"pl.col('{BASE_UNIT}').cast(pl.Utf8).str.len_chars() <= 40",
        [BASE_UNIT],
    )

    assert _flagged_items(_read(broken_workbook), rule) == LONG_BASE_UNIT
    assert _flagged_items(_read(clean_workbook), rule) == []


# --------------------------------------------------- rule family: derivation

def test_a_derivation_rule_flags_exactly_the_rows_that_break_it(
    broken_workbook, clean_workbook
):
    """FOOD derives 01/01/01/0001. Two rows carry 99/99/99/9999."""
    rule = _expression_rule(
        "DERIVE_FOOD",
        f"pl.when(pl.col('{MERCH}') == 'FOOD')"
        f".then(pl.all_horizontal("
        f"    pl.col('{CATEGORY}') == '01',"
        f"    pl.col('{TEMPERATURE}') == '01',"
        f"    pl.col('{CONTAINER}') == '01',"
        f"    pl.col('{DISCOUNT}') == '0001'))"
        f".otherwise(True)",
        [CATEGORY, TEMPERATURE, CONTAINER, DISCOUNT],
    )

    assert _flagged_items(_read(broken_workbook), rule) == BROKEN_DERIVATION
    assert _flagged_items(_read(clean_workbook), rule) == []


def test_the_derivation_rule_does_not_fire_on_grocery_rows(broken_workbook):
    """GROCERY leaves those fields null; a rule that ignores the condition would
    flag all 17 of them."""
    df = _read(broken_workbook)
    rule = _expression_rule(
        "DERIVE_FOOD",
        f"pl.when(pl.col('{MERCH}') == 'FOOD')"
        f".then(pl.col('{CATEGORY}') == '01').otherwise(True)",
        [CATEGORY],
    )

    flagged = _flagged_items(df, rule)
    grocery = df.filter(pl.col(MERCH) == "GROCERY")[ITEM].to_list()
    assert not set(flagged) & set(grocery)


# ---------------------------------------------------- rule family: reference

def test_a_reference_rule_flags_the_value_outside_the_keyset(
    broken_workbook, clean_workbook, tmp_path, validator
):
    """The clean workbook defines the allowed set; the broken one is checked
    against it. That is the real relationship between the two files."""
    allowed = _read(clean_workbook).select([COUNTRY, REGION]).unique()
    reference = str(tmp_path / "country-region.csv")
    allowed.write_csv(reference)

    df = _read(broken_workbook)
    rule = ValidationRule(
        rule_name="FILTER_COUNTRY_REGION", type="reference_lookup",
        error_message="not a valid country/region pair",
        params={"reference_source": reference, "reference_format": "csv",
                "source_columns": [COUNTRY, REGION], "violate_when": "not_in_set"},
    )

    expressions, mapping = validator._process_reference_rules([rule], df)  # noqa: SLF001
    out = df.with_columns(expressions)
    alias = list(mapping)[0]
    flagged = [row[ITEM] for row in out.to_dicts() if row[alias] is not None]

    assert flagged == BAD_REGION


def test_a_single_column_reference_rule_flags_the_bad_key(
    broken_workbook, tmp_path, validator
):
    reference = str(tmp_path / "pvk.csv")
    pl.DataFrame({PVK: ["NORM"]}).write_csv(reference)

    df = _read(broken_workbook)
    rule = ValidationRule(
        rule_name="FILTER_PVK", type="reference_lookup",
        error_message="not an allowed purchasing value key",
        params={"reference_source": reference, "reference_format": "csv",
                "source_columns": [PVK], "violate_when": "not_in_set"},
    )

    expressions, mapping = validator._process_reference_rules([rule], df)  # noqa: SLF001
    out = df.with_columns(expressions)
    alias = list(mapping)[0]
    flagged = [row[ITEM] for row in out.to_dicts() if row[alias] is not None]

    assert flagged == BAD_PVK


# -------------------------------------------------- rule family: cross-table

def test_a_child_rule_flags_rows_using_the_parents_category(broken_workbook):
    """The child sheet has no Merchandise Category; the rule needs the join."""
    parent = _read(broken_workbook, "ArticleMaster").select([ITEM, MERCH])
    child = _read(broken_workbook, "ARTMasterPurchasing")
    joined = child.join(parent, on=ITEM, how="left")

    rule = _expression_rule(
        "DERIVE_PIR",
        f"pl.when(pl.col('{MERCH}') == 'FOOD')"
        f".then(pl.all_horizontal(pl.col('{PIR}') == 'PC',"
        f"                        pl.col('{PIR_CATEGORY}').cast(pl.Utf8) == '4'))"
        f".otherwise(True)",
        [PIR, PIR_CATEGORY],
    )

    assert _flagged_items(joined, rule) == BROKEN_CHILD_DERIVATION


def test_the_price_column_is_numeric_not_text(broken_workbook):
    child = _read(broken_workbook, "ARTMasterPurchasing")

    assert child[PRICE].dtype.is_numeric()


# ------------------------------------------------- the dialects, on real data

def test_four_dialects_of_the_length_rule_flag_identical_rows(broken_workbook):
    df = _read(broken_workbook)
    dialects = [
        f"pl.col('{BASE_UNIT}').cast(pl.Utf8).str.len_chars() <= 40",
        f"col('{BASE_UNIT}').cast(pl.Utf8).str.len_chars() <= 40",
        f"F.col('{BASE_UNIT}').cast(pl.Utf8).str.len_chars() <= 40",
        f"limit = 40\npl.col('{BASE_UNIT}').cast(pl.Utf8).str.len_chars() <= limit",
    ]

    results = [
        _flagged_items(df, _expression_rule(f"D{n}", expression, [BASE_UNIT]))
        for n, expression in enumerate(dialects)
    ]

    assert results == [LONG_BASE_UNIT] * len(dialects)


def test_an_escape_never_reaches_the_data(broken_workbook):
    df = _read(broken_workbook)
    rule = _expression_rule(
        "ESCAPE",
        "pl.__loader__.__init__.__globals__['__builtins__']['open']('pwned','w')",
        [BASE_UNIT],
    )

    with pytest.raises(RuleExpressionError, match="__globals__"):
        _flagged_items(df, rule)


def test_a_rule_that_cannot_run_is_not_reported_as_clean(broken_workbook):
    """The failure mode this replaces: warn, return [], report zero violations."""
    df = _read(broken_workbook)
    rule = _expression_rule("TYPO", f"pl.col('{BASE_UNIT}').strr.len_chars() <= 40",
                            [BASE_UNIT])

    with pytest.raises(RuleExpressionError, match="TYPO"):
        _flagged_items(df, rule)


# ------------------------------------------------------- the real workbooks

WORKBOOK_DIR = os.environ.get(
    "ART41_WORKBOOK_DIR", os.path.expanduser("~/Downloads/enhanceDF")
)
REAL_CLEAN = os.path.join(WORKBOOK_DIR, "ART41.00_500_valid_rules.xlsx")
REAL_BROKEN = os.path.join(WORKBOOK_DIR, "ART41.00_500_invalid_rules.xlsx")

real_workbooks = pytest.mark.skipif(
    not (os.path.exists(REAL_CLEAN) and os.path.exists(REAL_BROKEN)),
    reason=f"real ART41.00 workbooks not found under {WORKBOOK_DIR}; "
           "set ART41_WORKBOOK_DIR to run these",
)


@real_workbooks
def test_real_workbooks_read_the_same_shape():
    clean, broken = _read(REAL_CLEAN), _read(REAL_BROKEN)

    assert clean.height == broken.height == 506
    assert clean.columns[0] == ITEM
    assert not any(c.startswith("__UNNAMED__") for c in clean.columns)


@real_workbooks
def test_real_length_rule_flags_exactly_two_rows():
    rule = _expression_rule(
        "LENGTH_40", f"pl.col('{BASE_UNIT}').cast(pl.Utf8).str.len_chars() <= 40",
        [BASE_UNIT],
    )

    assert _flagged_items(_read(REAL_BROKEN), rule) == LONG_BASE_UNIT
    assert _flagged_items(_read(REAL_CLEAN), rule) == []


@real_workbooks
def test_real_derivation_rule_flags_exactly_two_of_a_hundred_food_rows():
    rule = _expression_rule(
        "DERIVE_FOOD",
        f"pl.when(pl.col('{MERCH}') == 'FOOD')"
        f".then(pl.all_horizontal("
        f"    pl.col('{CATEGORY}') == '01',"
        f"    pl.col('{TEMPERATURE}') == '01',"
        f"    pl.col('{CONTAINER}') == '01',"
        f"    pl.col('{DISCOUNT}') == '0001'))"
        f".otherwise(True)",
        [CATEGORY, TEMPERATURE, CONTAINER, DISCOUNT],
    )
    broken = _read(REAL_BROKEN)

    assert broken.filter(pl.col(MERCH) == "FOOD").height == 100
    assert _flagged_items(broken, rule) == BROKEN_DERIVATION
    assert _flagged_items(_read(REAL_CLEAN), rule) == []


@real_workbooks
def test_real_assignment_rule_flags_the_seeded_derivation_breaks(tmp_path):
    """An Assignment Rule needs a value mapping, which the template screen does
    not show. Here the clean workbook *is* the mapping: FOOD -> 01, GROCERY ->
    00. Checking the broken workbook against it finds exactly the two rows whose
    derived category was tampered with."""
    from app.layer4_frameworks.providers.validation.polars_validator_provider import (
        PolarsValidatorProvider,
    )

    clean, broken = _read(REAL_CLEAN), _read(REAL_BROKEN)
    mapping_path = str(tmp_path / "category-map.csv")
    clean.select([MERCH, CATEGORY]).unique().drop_nulls().write_csv(mapping_path)

    provider = PolarsValidatorProvider(logger=_Logger(), storage=_LocalStorage())
    rule = ValidationRule(
        rule_name="ASSIGN_ARTICLE_CATEGORY", type="assignment",
        error_message="articleCategory does not match merchandiseCategory",
        params={"source_column": MERCH, "target_column": CATEGORY,
                "mapping_source": mapping_path, "mapping_format": "csv",
                "mode": "match"})

    def flagged(frame):
        expressions, mapping = provider._process_assignment_rules([rule], frame)  # noqa: SLF001
        alias = list(mapping)[0]
        out = frame.with_columns(expressions)
        return [row[ITEM] for row in out.to_dicts() if row[alias] is not None]

    assert flagged(broken) == BROKEN_DERIVATION
    assert flagged(clean) == []


@real_workbooks
def test_real_workbooks_differ_only_where_expected():
    """If this fails the fixture above is modelling the wrong thing."""
    clean, broken = _read(REAL_CLEAN), _read(REAL_BROKEN)
    changed = {
        column
        for column in clean.columns
        if column in broken.columns
        and clean[column].cast(pl.Utf8).fill_null("~").to_list()
        != broken[column].cast(pl.Utf8).fill_null("~").to_list()
    }

    assert changed == {CATEGORY, BASE_UNIT, TEMPERATURE, CONTAINER, DISCOUNT,
                       REGION, PVK}
