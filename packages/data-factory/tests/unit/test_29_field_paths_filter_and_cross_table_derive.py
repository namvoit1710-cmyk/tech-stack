"""Three gaps between the ART41.00 rule trace and what the engine could run.

1. Every violation now names the ``SECTION.field`` path the template is
   configured against, not just the spreadsheet column label. The doc is
   explicit that the frontend keys errors on that path.
2. The Filter Rule -- six of them assigned, none runnable. The only ``filter``
   the engine had dropped rows, which the requirement forbids in as many words:
   a Filter Rule must not silently remove the item from the file, it must report
   a violation naming both the target and the source that constrained it.
3. Cross-table derivation. ARTM_FOOD tests ``ArticleMaster.articleGroup`` and
   writes into two other tables, so the condition and the target do not live on
   the same frame.
"""

import polars as pl
import pytest

from app.layer1_domain.entities.validation import ValidationRule
from app.layer3_adapters.controllers.restful.v1.dtos.rule_spec import (
    RuleSpecError,
    validate_validation_rules,
)
from app.layer4_frameworks.providers.formats.csv_reader import CsvSourceReader
from app.layer4_frameworks.providers.formats.field_paths import (
    build_field_paths,
    field_name_from_label,
    normalize_section,
    spread_sections,
)
from app.layer4_frameworks.providers.transformation.polars_schema_transformer_provider import (
    PolarsSchemaTransformerProvider,
)
from app.layer4_frameworks.providers.validation.polars_validator_provider import (
    PolarsValidatorProvider,
)
from app.layer4_frameworks.providers.validation.rule_handlers import RuleFactory


class _Logger:
    def __init__(self):
        self.warnings, self.errors = [], []

    def info(self, *a, **k): pass
    def debug(self, *a, **k): pass
    def warning(self, m, *a, **k): self.warnings.append(str(m))
    def error(self, m, *a, **k): self.errors.append(str(m))


class _LocalStorage:
    def __init__(self):
        self._readers = {"csv": CsvSourceReader()}

    def download_and_read(self, file_path, file_format, version_id=None,
                          sheet_names=None, merge_sheets=False,
                          add_sheet_name_column=False, header_row=None):
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


def _write(frame, tmp_path, name):
    path = str(tmp_path / f"{name}.csv")
    frame.write_csv(path)
    return path


# ================================================== 1. SECTION.field paths

#: The real ART41.00 banner, as fastexcel hands it over.
BANNER = ["3", "Article", None, None, None, None, "General data", None, None]
LABELS = ["ItemID (Integer)", "Article (String)", "variant (String)",
          "Article Type (String)", "Merchandise Category (String)",
          "Article Category (String)", "Tax Class (String)",
          "Base Unit (String)", "With empties BOM (Boolean)"]


@pytest.mark.parametrize("label,expected", [
    ("Base Unit (String)", "baseUnit"),
    ("Article Type (String)", "articleType"),
    ("Merchandise Category (String)", "merchandiseCategory"),
    ("ItemID (Integer)", "itemID"),
    ("Validity Start Date (yyyy-MM-dd)", "validityStartDate"),
    ("Haz. Matl No. (String)", "hazMatlNo"),
    ("X-Plant Status Valid From (yyyy-MM-dd)", "xPlantStatusValidFrom"),
    ("", ""),
    (None, ""),
])
def test_a_column_label_becomes_a_field_name(label, expected):
    assert field_name_from_label(label) == expected


@pytest.mark.parametrize("text,expected", [
    ("General data", "GENERALDATA"),
    ("Article", "ARTICLE"),
    ("Purchasing value", "PURCHASINGVALUE"),
    (None, ""),
])
def test_a_banner_cell_becomes_a_section(text, expected):
    assert normalize_section(text) == expected


def test_a_merged_banner_cell_covers_the_columns_under_it():
    """The section is written once and blank across the rest of its range."""
    assert spread_sections(BANNER, 9) == [
        "", "Article", "Article", "Article", "Article", "Article",
        "General data", "General data", "General data",
    ]


def test_the_documented_example_path_comes_out_of_the_banner():
    paths = build_field_paths(LABELS, BANNER)
    assert paths["Base Unit (String)"] == "GENERALDATA.baseUnit"
    assert paths["Article Type (String)"] == "ARTICLE.articleType"
    assert paths["Article Category (String)"] == "ARTICLE.articleCategory"


def test_a_numeric_banner_cell_is_a_marker_not_a_section():
    """ART41.00 writes a 3 above ItemID; it must not become a path prefix."""
    assert build_field_paths(LABELS, BANNER)["ItemID (Integer)"] == "itemID"


def test_an_override_beats_the_camel_case_guess():
    """The heuristic is a fallback, not an authority."""
    paths = build_field_paths(
        ["Haz. Matl No. (String)"], ["General data"],
        overrides={"Haz. Matl No. (String)": "GENERALDATA.hazardousMaterialNumber"},
    )
    assert paths["Haz. Matl No. (String)"] == "GENERALDATA.hazardousMaterialNumber"


def test_without_a_banner_the_label_still_yields_a_name():
    assert build_field_paths(["Base Unit (String)"]) == {"Base Unit (String)": "baseUnit"}


def _violation_of(rule, frame):
    handler = RuleFactory().get_handler(rule.type)
    exprs = handler.parse_rule(rule, "err_0")
    out = frame.with_columns(exprs)
    column = exprs[0].meta.output_name()
    return out.filter(pl.col(column).is_not_null())[column][0]


def test_a_violation_reports_the_path_the_rules_are_written_in():
    rule = ValidationRule(rule_name="LENGTH_40", type="length",
                          error_message="The field length cannot exceed 40 characters",
                          params={"columns": ["Base Unit (String)"], "max": 3})
    rule.field_paths = {"Base Unit (String)": "GENERALDATA.baseUnit"}
    got = _violation_of(rule, pl.DataFrame({"Base Unit (String)": ["CARTON"]}))
    assert got["rule"] == "LENGTH_40"
    assert got["field"] == "Base Unit (String)"
    assert got["path"] == "GENERALDATA.baseUnit"


def test_without_a_map_the_path_falls_back_to_the_label():
    """No map is not an error -- the label is still the truest thing we have."""
    rule = ValidationRule(rule_name="LEN", type="length", error_message="too long",
                          params={"columns": ["baseUnit"], "max": 3})
    got = _violation_of(rule, pl.DataFrame({"baseUnit": ["CARTON"]}))
    assert got["path"] == "baseUnit"


def test_required_reports_a_path_too():
    frame = pl.DataFrame({"supplier": [None]})
    rule = ValidationRule(rule_name="REQ", type="required", error_message="required",
                          params={"columns": ["supplier"]})
    rule.field_paths = {"supplier": "PURCHASING.supplier"}
    assert _violation_of(rule, frame)["path"] == "PURCHASING.supplier"


# ============================================================ 2. Filter Rule

@pytest.fixture
def purchasing():
    """Row 3 names a supplier that does not belong to its purchasing org."""
    return pl.DataFrame({
        "purchasingOrganization": ["1000", "1000", "2000", "1000"],
        "supplier": ["S-A", "S-B", "S-A", None],
    })


@pytest.fixture
def supplier_map(tmp_path):
    return _write(pl.DataFrame({
        "purchasingOrganization": ["1000", "1000", "2000"],
        "supplier": ["S-A", "S-B", "S-C"],
    }), tmp_path, "suppliers")


def _filter_rule(mapping, **params):
    base = {"source_column": "purchasingOrganization", "target_column": "supplier",
            "mapping_source": mapping, "mapping_format": "csv"}
    base.update(params)
    return ValidationRule(
        rule_name="FILTER_SUPPLIER", type="filter",
        error_message="Supplier is not valid for the selected purchasing organization",
        params=base)


def _run_filter(validator, rule, frame):
    provider, _ = validator
    exprs, _ = provider._process_assignment_rules([rule], frame)
    out = frame.with_columns(exprs)
    column = exprs[0].meta.output_name()
    return out, column


def test_a_filter_flags_the_pair_that_is_not_permitted(validator, purchasing, supplier_map):
    out, column = _run_filter(validator, _filter_rule(supplier_map), purchasing)
    flagged = out.filter(pl.col(column).is_not_null())
    assert flagged.height == 1
    assert flagged["supplier"][0] == "S-A"
    assert flagged["purchasingOrganization"][0] == "2000"


def test_a_filter_does_not_remove_a_single_row(validator, purchasing, supplier_map):
    """The requirement forbids silently dropping the item from the file."""
    out, _ = _run_filter(validator, _filter_rule(supplier_map), purchasing)
    assert out.height == purchasing.height


def test_a_filter_violation_names_the_source_as_well_as_the_target(
        validator, purchasing, supplier_map):
    rule = _filter_rule(supplier_map)
    rule.field_paths = {"supplier": "PURCHASING.supplier",
                        "purchasingOrganization": "PURCHASING.purchasingOrganization"}
    out, column = _run_filter(validator, rule, purchasing)
    got = out.filter(pl.col(column).is_not_null())[column][0]
    assert got["ruleType"] == "FILTER"
    assert got["path"] == "PURCHASING.supplier"
    assert got["sourcePath"] == "PURCHASING.purchasingOrganization"
    assert got["sourceField"] == "purchasingOrganization"
    assert got["severity"] == "ERROR"
    assert got["value"] == "S-A"


def test_a_filter_severity_can_be_softened(validator, purchasing, supplier_map):
    out, column = _run_filter(
        validator, _filter_rule(supplier_map, severity="WARNING"), purchasing)
    got = out.filter(pl.col(column).is_not_null())[column][0]
    assert got["severity"] == "WARNING"


def test_severity_is_written_lowercase_and_reported_uppercase(
        validator, purchasing, supplier_map):
    """The boundary matches case-insensitively; the result uses the documented
    upper-case form, so a frontend switching on it has one spelling to handle."""
    validate_validation_rules([{
        "rule_name": "F", "type": "filter", "error_message": "no",
        "params": {"source_column": "a", "target_column": "b",
                   "mapping_source": "f-1", "severity": "warning"}}])
    out, column = _run_filter(
        validator, _filter_rule(supplier_map, severity="warning"), purchasing)
    assert out.filter(pl.col(column).is_not_null())[column][0]["severity"] == "WARNING"


def test_a_null_target_is_not_a_filter_violation(validator, purchasing, supplier_map):
    """Absence is what `required` is for."""
    out, column = _run_filter(validator, _filter_rule(supplier_map), purchasing)
    assert out.filter(pl.col("supplier").is_null())[column][0] is None


def test_a_source_the_mapping_never_heard_of_is_left_alone(validator, supplier_map):
    frame = pl.DataFrame({"purchasingOrganization": ["9999"], "supplier": ["S-Z"]})
    out, column = _run_filter(validator, _filter_rule(supplier_map), frame)
    assert out[column][0] is None


def test_the_boundary_accepts_a_filter_rule():
    validate_validation_rules([{
        "rule_name": "FILTER_SUPPLIER", "type": "filter", "error_message": "no",
        "params": {"source_column": "a", "target_column": "b",
                   "mapping_source": "f-1", "severity": "ERROR"}}])


def test_the_boundary_refuses_a_filter_without_its_mapping():
    with pytest.raises(RuleSpecError):
        validate_validation_rules([{
            "rule_name": "F", "type": "filter", "error_message": "no",
            "params": {"source_column": "a", "target_column": "b"}}])


def test_the_boundary_refuses_an_unknown_severity():
    with pytest.raises(RuleSpecError):
        validate_validation_rules([{
            "rule_name": "F", "type": "filter", "error_message": "no",
            "params": {"source_column": "a", "target_column": "b",
                       "mapping_source": "f-1", "severity": "FATAL"}}])


# =================================== 2b. numbers compare by value, not spelling

def test_a_whole_float_matches_the_same_number_written_without_its_zero(
        validator, tmp_path):
    """polars writes 8.0 to CSV as 8, so a mapping carrying 8.0 and a column
    carrying 8 are the same number spelled two ways. Comparing them as text
    made the rule match nothing at all, which reads exactly like a clean file."""
    data = pl.DataFrame({"netPrice": [8, 107, 55], "effectivePrice": [8, 999, 55]})
    mapping = _write(pl.DataFrame({"netPrice": [8.0, 107.0],
                                   "effectivePrice": [8.0, 107.0]}),
                     tmp_path, "prices")
    rule = ValidationRule(
        rule_name="ASSIGN_EP", type="assignment", error_message="mismatch",
        params={"source_column": "netPrice", "target_column": "effectivePrice",
                "mapping_source": mapping, "mapping_format": "csv", "mode": "match"})
    provider, _ = validator
    exprs, _ = provider._process_assignment_rules([rule], data)
    out = data.with_columns(exprs)
    column = exprs[0].meta.output_name()
    flagged = out.filter(pl.col(column).is_not_null())
    assert flagged.height == 1
    assert flagged["netPrice"][0] == 107


def test_the_reported_value_keeps_the_spelling_the_file_used(validator, tmp_path):
    """Comparison is normalised; what the user is shown is not."""
    data = pl.DataFrame({"netPrice": [107], "effectivePrice": [999]})
    mapping = _write(pl.DataFrame({"netPrice": [107.0], "effectivePrice": [107.0]}),
                     tmp_path, "prices2")
    rule = ValidationRule(
        rule_name="ASSIGN_EP", type="assignment", error_message="mismatch",
        params={"source_column": "netPrice", "target_column": "effectivePrice",
                "mapping_source": mapping, "mapping_format": "csv", "mode": "match"})
    provider, _ = validator
    exprs, _ = provider._process_assignment_rules([rule], data)
    out = data.with_columns(exprs)
    column = exprs[0].meta.output_name()
    assert out.filter(pl.col(column).is_not_null())[column][0]["value"] == "999"


def test_a_code_that_looks_like_a_number_is_not_treated_as_one(validator, tmp_path):
    """0001 and 1 are different purchasing organisations. Reading either as a
    number would merge them, so only a real numeric dtype compares numerically."""
    data = pl.DataFrame({"org": ["0001", "1"], "supplier": ["S-A", "S-A"]})
    mapping = _write(pl.DataFrame({"org": ["0001"], "supplier": ["S-A"]}),
                     tmp_path, "orgs")
    rule = ValidationRule(
        rule_name="F", type="filter", error_message="no",
        params={"source_column": "org", "target_column": "supplier",
                "mapping_source": mapping, "mapping_format": "csv"})
    provider, _ = validator
    exprs, _ = provider._process_assignment_rules([rule], data)
    out = data.with_columns(exprs)
    column = exprs[0].meta.output_name()
    # "1" is not a source the mapping knows, so it is not judged; "0001" is fine
    assert out[column].null_count() == 2


def test_a_text_column_still_compares_case_insensitively(validator, tmp_path):
    data = pl.DataFrame({"type": ["HAWA"], "cls": ["3100"]})
    mapping = _write(pl.DataFrame({"type": ["hawa"], "cls": ["3100"]}),
                     tmp_path, "types")
    rule = ValidationRule(
        rule_name="A", type="assignment", error_message="no",
        params={"source_column": "type", "target_column": "cls",
                "mapping_source": mapping, "mapping_format": "csv", "mode": "match"})
    provider, _ = validator
    exprs, _ = provider._process_assignment_rules([rule], data)
    assert data.with_columns(exprs)[exprs[0].meta.output_name()][0] is None


# ================================================ 3. cross-table derivation

@pytest.fixture
def article_master(tmp_path):
    """The table the ARTM_FOOD condition is written against."""
    return _write(pl.DataFrame({
        "ItemID": [1, 2, 3],
        "articleGroup": ["FOOD", "NONFOOD", "FOOD"],
    }), tmp_path, "ArticleMaster")


def test_a_derivation_fires_on_its_own_table(transformer):
    """The four ArticleMaster targets of ARTM_FOOD, which need no join."""
    provider, _ = transformer
    frame = pl.DataFrame({"articleGroup": ["FOOD", "NONFOOD"]})
    out = provider._apply_rules(frame, [{
        "type": "derive",
        "params": {
            "when": {"column": "articleGroup", "operator": "EQUAL", "value": "FOOD"},
            "then": [{"column": "articleCategory", "value": "01"},
                     {"column": "container", "value": "01"},
                     {"column": "discountInKindEligibility", "value": "0001"},
                     {"column": "temperature", "value": "01"}],
        }}])
    assert out["articleCategory"].to_list() == ["01", None]
    assert out["temperature"].to_list() == ["01", None]


def test_a_derivation_reaches_a_condition_that_lives_on_another_table(
        transformer, article_master):
    """ARTM_FOOD writes into ARTMasterPurchasing, which has no articleGroup.

    Joining the condition column in, deriving, then dropping it is the composed
    form of a cross-table rule -- the child table never needs to carry the
    parent field in its own file.
    """
    provider, _ = transformer
    purchasing = pl.DataFrame({"ItemID": [1, 2, 3],
                               "purchasingInfoRecord": [None, None, None]})

    out = provider._apply_rules(purchasing, [
        {"type": "join_reference",
         "params": {"file_path": article_master, "file_format": "csv",
                    "on": ["ItemID"], "columns": ["articleGroup"], "how": "left"}},
        {"type": "derive",
         "params": {"when": {"column": "articleGroup", "operator": "EQUAL",
                             "value": "FOOD"},
                    "then": [{"column": "purchasingInfoRecord", "value": "PC"},
                             {"column": "purchasingInfoRecordCategory", "value": "4"}]}},
        {"type": "drop_columns", "params": {"columns": ["articleGroup"]}},
    ])

    assert out["purchasingInfoRecord"].to_list() == ["PC", None, "PC"]
    assert out["purchasingInfoRecordCategory"].to_list() == ["4", None, "4"]
    assert "articleGroup" not in out.columns


def test_the_joined_condition_column_does_not_survive_into_the_output(
        transformer, article_master):
    """A derived child table must not gain the parent column as a side effect."""
    provider, _ = transformer
    purchasing = pl.DataFrame({"ItemID": [1, 2, 3]})
    out = provider._apply_rules(purchasing, [
        {"type": "join_reference",
         "params": {"file_path": article_master, "file_format": "csv",
                    "on": ["ItemID"], "columns": ["articleGroup"], "how": "left"}},
        {"type": "derive",
         "params": {"when": {"column": "articleGroup", "operator": "EQUAL",
                             "value": "FOOD"},
                    "then": [{"column": "effectivePrice", "value": "8"}]}},
        {"type": "drop_columns", "params": {"columns": ["articleGroup"]}},
    ])
    assert out.columns == ["ItemID", "effectivePrice"]
    assert out["effectivePrice"].to_list() == ["8", None, "8"]
