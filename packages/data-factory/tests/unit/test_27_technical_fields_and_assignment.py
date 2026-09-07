"""Three things the persistence trace asks for that the engine could not do.

1. A validation result that says *which field* failed and *what it held*. The
   writer flattened every violation to its message, so a caller could say "this
   row is wrong" but not point at the cell.
2. The technical identifiers — objectID, reqID, itemID, taskID, MDGStatus,
   MDGModifyType, the table/parent names — without which a flattened table
   cannot be put back together into the multi-level object it came from.
3. Assignment Rules: a source field determines a target field. The template
   screen shows the field relationship but not the value mapping behind it, so
   the mapping arrives as data.
"""

import io
import json

import polars as pl
import pytest

from app.layer1_domain.entities.validation import ValidationRule
from app.layer3_adapters.controllers.restful.v1.dtos.rule_spec import (
    RuleSpecError,
    validate_schema_transform_rules,
    validate_validation_rules,
)
from app.layer4_frameworks.providers.formats.csv_reader import CsvSourceReader
from app.layer4_frameworks.providers.transformation.polars_schema_transformer_provider import (
    PolarsSchemaTransformerProvider,
)
from app.layer4_frameworks.providers.validation.polars_validator_provider import (
    PolarsValidatorProvider,
    _StreamingResultWriter,
)
from app.layer4_frameworks.providers.validation.rule_handlers import RuleFactory


class _Logger:
    def __init__(self):
        self.warnings = []
        self.errors = []

    def info(self, *a, **k): pass
    def debug(self, *a, **k): pass

    def warning(self, message, *a, **k):
        self.warnings.append(str(message))

    def error(self, message, *a, **k):
        self.errors.append(str(message))


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


def _write_csv(frame, tmp_path, name):
    path = str(tmp_path / f"{name}.csv")
    frame.write_csv(path)
    return path


# =========================================================== 1. the writer

def _result_rows(frame, rules):
    factory = RuleFactory()
    expressions, aliases = [], []
    for index, rule in enumerate(rules):
        produced = factory.get_handler(rule.type).parse_rule(rule, f"err_{index}")
        expressions += produced
        aliases += [e.meta.output_name() for e in produced]

    writer = _StreamingResultWriter(_Logger(), result_mode="errors_only")
    writer.write(frame.with_columns(expressions), aliases)
    text = io.open(writer.file_path, encoding="utf-8").read()
    writer.cleanup()
    return pl.read_csv(io.StringIO(text)) if text.strip() else None


@pytest.fixture
def flawed():
    return pl.DataFrame({
        "__source_row": [1, 2, 3],
        "code": ["AB", "TOOLONGVALUE", None],
        "qty": ["5", "150", None],
    })


@pytest.fixture
def two_rules():
    return [
        ValidationRule(rule_name="LEN", type="length", error_message="too long",
                       params={"columns": ["code"], "max": 4}),
        ValidationRule(rule_name="RNG", type="range", error_message="out of range",
                       params={"columns": ["qty"], "max": 100}),
    ]


def test_the_result_now_says_which_field_failed_and_what_it_held(flawed, two_rules):
    out = _result_rows(flawed, two_rules)

    assert "validation_errors" in out.columns
    errors = json.loads(out["validation_errors"][0])
    assert [e["field"] for e in errors] == ["code", "qty"]
    assert [e["value"] for e in errors] == ["TOOLONGVALUE", "150"]
    assert [e["rule"] for e in errors] == ["LEN", "RNG"]
    assert [e["message"] for e in errors] == ["too long", "out of range"]


def test_the_source_row_survives_so_an_error_can_point_at_a_cell(flawed, two_rules):
    out = _result_rows(flawed, two_rules)

    assert out["__source_row"].to_list() == [2]


def test_the_existing_message_columns_are_unchanged(flawed, two_rules):
    """Anything already reading err_<n>_<column> keeps working."""
    out = _result_rows(flawed, two_rules)

    assert out["err_0_code"].to_list() == ["too long"]
    assert out["err_1_qty"].to_list() == ["out of range"]
    assert out["has_validation_error"].to_list() == [True]


def test_a_row_carries_only_the_violations_it_actually_has(two_rules):
    frame = pl.DataFrame({"__source_row": [1], "code": ["TOOLONGVALUE"], "qty": ["5"]})

    errors = json.loads(_result_rows(frame, two_rules)["validation_errors"][0])

    assert len(errors) == 1
    assert errors[0]["field"] == "code"


def test_a_clean_file_produces_no_result_rows(two_rules):
    frame = pl.DataFrame({"__source_row": [1], "code": ["AB"], "qty": ["5"]})

    assert _result_rows(frame, two_rules) is None


# ================================================= 2. the technical fields

@pytest.fixture
def rows(tmp_path):
    return _write_csv(
        pl.DataFrame({"article": ["A100", "A200", "A300"], "plant": ["1000", "1000", "2000"]}),
        tmp_path, "rows")


def _apply(transformer, path, rule):
    provider, logger = transformer
    frame = CsvSourceReader().read(path)
    return provider._apply_rules(frame, [rule]), logger  # noqa: SLF001


def test_the_change_request_identifiers_are_stamped_on_every_row(transformer, rows):
    out, _ = _apply(transformer, rows, {
        "type": "add_technical_fields",
        "params": {"object_id": "OBJ-001", "req_id": "CR-001", "task_id": "T-1",
                   "status": "10", "modify_type": "C",
                   "table_name": "ArticleMaster"}})

    assert out["objectID"].to_list() == ["OBJ-001"] * 3
    assert out["reqID"].to_list() == ["CR-001"] * 3
    assert out["taskID"].to_list() == ["T-1"] * 3
    assert out["MDGStatus"].to_list() == ["10"] * 3
    assert out["MDGModifyType"].to_list() == ["C"] * 3
    assert out["tableName"].to_list() == ["ArticleMaster"] * 3


def test_item_id_and_source_row_are_per_row(transformer, rows):
    out, _ = _apply(transformer, rows, {
        "type": "add_technical_fields",
        "params": {"item_id": "itemID", "source_row": "sourceRow"}})

    assert out["itemID"].to_list() == [1, 2, 3]
    assert out["sourceRow"].to_list() == [1, 2, 3]


def test_source_row_can_be_offset_past_the_banner_rows(transformer, rows):
    """The template puts the real header on row 3, so data starts at row 4 —
    which is the number the user sees in the spreadsheet."""
    out, _ = _apply(transformer, rows, {
        "type": "add_technical_fields",
        "params": {"source_row": "sourceRow", "source_row_offset": 4}})

    assert out["sourceRow"].to_list() == [4, 5, 6]


def test_a_column_that_already_has_a_value_is_not_overwritten(transformer, tmp_path):
    """A workbook that already carries itemID keeps its own, rather than being
    silently renumbered under it."""
    path = _write_csv(pl.DataFrame({"itemID": [7, 8], "article": ["A", "B"]}),
                      tmp_path, "with_ids")

    out, logger = _apply(transformer, path, {
        "type": "add_technical_fields", "params": {"item_id": "itemID"}})

    # Read back as text by the CSV reader; what matters is that 7 and 8 survived
    # rather than being replaced by a fresh 1, 2.
    assert [str(v) for v in out["itemID"].to_list()] == ["7", "8"]


def test_the_column_names_can_be_overridden(transformer, rows):
    out, _ = _apply(transformer, rows, {
        "type": "add_technical_fields",
        "params": {"object_id": "OBJ-1", "names": {"object_id": "OBJECT_ID"}}})

    assert "OBJECT_ID" in out.columns
    assert "objectID" not in out.columns


def test_nothing_is_added_when_nothing_is_asked_for(transformer, rows):
    out, _ = _apply(transformer, rows, {"type": "add_technical_fields", "params": {}})

    assert out.columns == ["article", "plant"]


# ==================================================== 3. the Assignment Rule

@pytest.fixture
def valuation_map(tmp_path):
    """Assignment Rule #1: articleType -> valuationClass."""
    return _write_csv(
        pl.DataFrame({"articleType": ["ZFOOD", "ZNON"],
                      "valuationClass": ["3000", "3100"]}),
        tmp_path, "valuation_map")


def _assignment(validator, frame, rule):
    provider, logger = validator
    expressions, mapping = provider._process_assignment_rules([rule], frame)  # noqa: SLF001
    if not expressions:
        return None, logger
    out = frame.with_columns(expressions)
    alias = list(mapping)[0]
    return [row[alias] for row in out.to_dicts()], logger


def _rule(**params):
    params.setdefault("mapping_format", "csv")
    return ValidationRule(rule_name="ASSIGN_VALUATION", type="assignment",
                          error_message="valuationClass does not match articleType",
                          params=params)


def test_match_mode_flags_the_target_that_disagrees_with_its_source(
    validator, valuation_map
):
    frame = pl.DataFrame({
        "articleType": ["ZFOOD", "ZNON", "ZFOOD"],
        "valuationClass": ["3000", "3100", "9999"],
    })

    flagged, _ = _assignment(validator, frame, _rule(
        source_column="articleType", target_column="valuationClass",
        mapping_source=valuation_map))

    assert [f is not None for f in flagged] == [False, False, True]
    assert flagged[2]["field"] == "valuationClass"
    assert flagged[2]["value"] == "9999"
    assert flagged[2]["rule"] == "ASSIGN_VALUATION"


def test_allowed_mode_accepts_any_of_the_values_mapped_for_that_source(
    validator, tmp_path
):
    """Assignment Rule #9: a sales org permits several distribution channels."""
    mapping = _write_csv(
        pl.DataFrame({"salesOrg": ["1000", "1000", "2000"],
                      "channel": ["10", "20", "10"]}),
        tmp_path, "channels")
    frame = pl.DataFrame({"salesOrg": ["1000", "1000", "2000"],
                          "channel": ["10", "20", "20"]})

    flagged, _ = _assignment(validator, frame, _rule(
        source_column="salesOrg", target_column="channel",
        mapping_source=mapping, mode="allowed"))

    # ("2000","20") is not a mapped pair even though both values exist.
    assert [f is not None for f in flagged] == [False, False, True]


def test_a_source_the_mapping_has_never_heard_of_is_not_judged_here(
    validator, valuation_map
):
    """That is a reference_lookup question about the source. Failing it here too
    would report one problem twice."""
    frame = pl.DataFrame({"articleType": ["ZUNKNOWN"], "valuationClass": ["9999"]})

    flagged, _ = _assignment(validator, frame, _rule(
        source_column="articleType", target_column="valuationClass",
        mapping_source=valuation_map))

    assert flagged == [None]


def test_an_empty_target_is_left_to_the_required_rule(validator, valuation_map):
    frame = pl.DataFrame({"articleType": ["ZFOOD"], "valuationClass": [None]})

    flagged, _ = _assignment(validator, frame, _rule(
        source_column="articleType", target_column="valuationClass",
        mapping_source=valuation_map))

    assert flagged == [None]


def test_the_mapping_columns_can_be_named_differently(validator, tmp_path):
    mapping = _write_csv(
        pl.DataFrame({"from": ["ZFOOD"], "to": ["3000"]}), tmp_path, "renamed")
    frame = pl.DataFrame({"articleType": ["ZFOOD"], "valuationClass": ["9999"]})

    flagged, _ = _assignment(validator, frame, _rule(
        source_column="articleType", target_column="valuationClass",
        mapping_source=mapping, mapping_source_column="from",
        mapping_target_column="to"))

    assert flagged[0] is not None


def test_a_rule_naming_a_missing_column_is_skipped_not_fatal(validator, valuation_map):
    frame = pl.DataFrame({"articleType": ["ZFOOD"]})

    flagged, logger = _assignment(validator, frame, _rule(
        source_column="articleType", target_column="valuationClass",
        mapping_source=valuation_map))

    assert flagged is None
    assert any("valuationClass" in w for w in logger.warnings)


def test_a_mapping_without_the_expected_columns_is_reported(validator, tmp_path):
    mapping = _write_csv(pl.DataFrame({"a": ["x"], "b": ["y"]}), tmp_path, "wrong")
    frame = pl.DataFrame({"articleType": ["ZFOOD"], "valuationClass": ["3000"]})

    flagged, logger = _assignment(validator, frame, _rule(
        source_column="articleType", target_column="valuationClass",
        mapping_source=mapping))

    assert flagged is None
    assert any("mapping needs columns" in e for e in logger.errors)


def test_a_missing_mapping_file_does_not_take_the_run_down(validator, tmp_path):
    frame = pl.DataFrame({"articleType": ["ZFOOD"], "valuationClass": ["3000"]})

    flagged, logger = _assignment(validator, frame, _rule(
        source_column="articleType", target_column="valuationClass",
        mapping_source=str(tmp_path / "nope.csv")))

    assert flagged is None
    assert any("could not load mapping" in e for e in logger.errors)


def test_case_and_padding_do_not_change_the_verdict(validator, valuation_map):
    frame = pl.DataFrame({"articleType": [" zfood "], "valuationClass": ["3000"]})

    flagged, _ = _assignment(validator, frame, _rule(
        source_column="articleType", target_column="valuationClass",
        mapping_source=valuation_map))

    assert flagged == [None]


# ------------------------------------------------------- boundary agreement

def test_the_new_types_are_accepted_at_the_boundary():
    validate_validation_rules([{
        "rule_name": "A", "type": "assignment",
        "params": {"source_column": "a", "target_column": "b",
                   "mapping_source": "m", "mode": "match"}}])
    validate_schema_transform_rules([{
        "type": "add_technical_fields",
        "params": {"object_id": "OBJ-1", "item_id": "itemID"}}])


@pytest.mark.parametrize("payload,expected", [
    ({"type": "assignment", "params": {"source_column": "a"}},
     "missing required param"),
    ({"type": "assignment",
      "params": {"source_column": "a", "target_column": "b",
                 "mapping_source": "m", "mode": "derive"}}, "mode"),
    ({"type": "assignment",
      "params": {"source_column": "a", "target_column": "b",
                 "mapping_source": "m", "mappings": {}}}, "unknown param"),
])
def test_a_malformed_assignment_is_refused(payload, expected):
    with pytest.raises(RuleSpecError, match=expected):
        validate_validation_rules([payload])


def test_a_malformed_technical_fields_rule_is_refused():
    with pytest.raises(RuleSpecError, match="unknown param"):
        validate_schema_transform_rules([
            {"type": "add_technical_fields", "params": {"objectId": "OBJ-1"}}])
