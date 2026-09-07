"""The boundary must refuse any rule the engine cannot actually run.

The failure this prevents: a rule with a typo'd type or param key used to be
logged and skipped inside the engine, so the caller got HTTP 200 with zero
violations. A validation that quietly checked nothing looks exactly like a clean
file. Every case here asserts that such a payload is rejected instead.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import (
    bundle_controller,
    data_validation_controller,
    schema_transform_controller,
)
from app.layer3_adapters.controllers.restful.v1.dtos.rule_spec import (
    RuleSpecError,
    SCHEMA_TRANSFORM_RULE_SPECS,
    VALIDATION_RULE_SPECS,
    validate_schema_transform_rules,
    validate_validation_rules,
)


# --------------------------------------------------------------- unit level


def _expect(rules, checker=validate_validation_rules):
    with pytest.raises(RuleSpecError) as caught:
        checker(rules)
    return str(caught.value)


def test_an_unknown_rule_type_is_refused_and_the_options_are_listed():
    message = _expect([{"rule_name": "R", "type": "regex_match", "params": {}}])

    assert "unknown rule type 'regex_match'" in message
    assert "reference_lookup" in message  # the caller is told what IS supported


def test_the_typo_that_started_all_this():
    """`source_column` for `source_columns` used to disable the rule silently."""
    message = _expect([{
        "rule_name": "FILTER", "type": "reference_lookup",
        "params": {"reference_source": "ref", "source_column": ["a", "b"]},
    }])

    assert "unknown param(s) ['source_column']" in message


def test_a_missing_required_param_is_refused():
    message = _expect([{"rule_name": "R", "type": "required", "params": {}}])

    assert "missing required param 'columns'" in message


def test_an_expression_rule_without_an_expression_is_refused():
    message = _expect([{"rule_name": "R", "type": "expression",
                        "params": {"columns": ["a"]}}])

    assert "missing required param 'expression'" in message


def test_a_one_of_group_must_be_satisfied():
    message = _expect([{"rule_name": "R", "type": "reference_lookup",
                        "params": {"columns": ["a"]}}])

    assert "needs one of reference_source, reference_id" in message


def test_every_problem_is_reported_in_one_pass():
    """One round trip should be enough to fix the whole payload."""
    message = _expect([
        {"rule_name": "A", "type": "nope", "params": {}},
        {"rule_name": "B", "type": "required", "params": {}},
        {"rule_name": "C", "type": "unique", "params": {"colums": ["x"]}},
    ])

    assert "rules[0]" in message and "rules[1]" in message and "rules[2]" in message


# ------------------------------------------------- input-variable exceptions


@pytest.mark.parametrize("rules,fragment", [
    ("not-a-list", "must be a list"),
    ({"type": "required"}, "must be a list"),
    (42, "must be a list"),
    ([None], "must be an object"),
    (["required"], "must be an object"),
    ([{"rule_name": "R"}], "'type' is required"),
    ([{"type": ""}], "'type' is required"),
    ([{"type": "   "}], "'type' is required"),
    ([{"type": 7}], "'type' must be a string"),
    ([{"type": "required", "params": "columns"}], "'params' must be an object"),
    ([{"type": "required", "params": 5}], "'params' must be an object"),
])
def test_malformed_input_is_refused_not_crashed_on(rules, fragment):
    assert fragment in _expect(rules)


def test_none_is_accepted_because_rules_are_optional():
    assert validate_validation_rules(None) is None


def test_an_empty_list_is_accepted():
    assert validate_validation_rules([]) == []


def test_params_may_be_omitted_when_nothing_is_required():
    assert validate_schema_transform_rules([{"type": "build_nested_json"}])


def test_params_may_be_null():
    assert validate_schema_transform_rules(
        [{"type": "build_nested_json", "params": None}])


# --------------------------------------------------------------- type checks


@pytest.mark.parametrize("value,fragment", [
    ("baseUnit", "must be list"),
    (5, "must be list"),
    ({"a": 1}, "must be list"),
    ([1, 2], "must contain only string values; wrong type at index 0"),
    (["ok", None], "wrong type at index 1"),
])
def test_a_column_list_must_really_be_a_list_of_names(value, fragment):
    message = _expect([{"rule_name": "R", "type": "required",
                        "params": {"columns": value}}])
    assert fragment in message


def test_a_bad_dtype_is_refused_rather_than_silently_becoming_text():
    """This was the silent-corruption case: an unknown dtype fell back to Utf8."""
    message = _expect([{"type": "cast", "params": {"column": "x", "dtype": "int32ish"}}],
                      validate_schema_transform_rules)

    assert "'dtype' must be one of" in message
    assert "decimal" in message


def test_a_bad_enum_value_is_refused():
    message = _expect([{"rule_name": "R", "type": "reference_lookup",
                        "params": {"reference_source": "r", "columns": ["a"],
                                   "violate_when": "maybe"}}])

    assert "'violate_when' must be one of in_set, not_in_set" in message


def test_a_negative_row_index_is_refused():
    message = _expect([{"type": "add_row_index", "params": {"offset": -1}}],
                      validate_schema_transform_rules)

    assert "'offset' must be >= 0" in message


def test_a_boolean_is_not_accepted_where_an_integer_is_required():
    """bool subclasses int in Python; `header_row: true` must not read as 1."""
    message = _expect([{"type": "join_reference",
                        "params": {"file_id": "f", "on": ["k"], "header_row": True}}],
                      validate_schema_transform_rules)

    assert "'header_row' must be integer, got boolean" in message


# ------------------------------------------------------------ derive checks


def test_a_derive_condition_without_a_column_is_refused():
    message = _expect([{"type": "derive", "params": {
        "when": {"value": "FOOD"}, "then": [{"column": "x", "value": "1"}]}}],
        validate_schema_transform_rules)

    assert "needs a 'column'" in message


def test_an_unknown_derive_operator_is_refused():
    message = _expect([{"type": "derive", "params": {
        "when": {"column": "g", "operator": "LIKE", "value": "F"},
        "then": [{"column": "x", "value": "1"}]}}],
        validate_schema_transform_rules)

    assert "unknown operator 'LIKE'" in message
    assert "STARTS_WITH" in message


def test_a_bad_match_mode_is_refused():
    message = _expect([{"type": "derive", "params": {
        "when": {"match": "EITHER", "conditions": [{"column": "g", "value": "F"}]},
        "then": [{"column": "x", "value": "1"}]}}],
        validate_schema_transform_rules)

    assert "'when.match' must be ALL or ANY" in message


def test_a_well_formed_derive_passes():
    assert validate_schema_transform_rules([{"type": "derive", "params": {
        "when": {"match": "ALL", "conditions": [
            {"column": "articleGroup", "operator": "EQUAL", "value": "FOOD"},
            {"column": "plant", "operator": "IN", "value": ["RF11"]}]},
        "then": [{"column": "articleCategory", "value": "01"}],
        "only_when_empty": True}}])


# ------------------------------------------------------------ every rule type


def test_every_declared_validation_type_accepts_a_minimal_valid_rule():
    minimal = {
        "required": {"columns": ["a"]},
        "expression": {"expression": "pl.col('a').is_not_null()"},
        "unique": {"columns": ["a"]},
        "set_unique": {"columns": ["a"]},
        "fuzzy_unique": {"columns": ["a"]},
        "reference_lookup": {"reference_source": "r", "columns": ["a"]},
        "semantic_unique": {"columns": ["a"]},
        "composite_unique": {"columns": ["a", {"column": "b", "match": "fuzzy"}]},
        "length": {"columns": ["a"], "max": 40},
        "pattern": {"columns": ["a"], "pattern": "^x$"},
        "range": {"columns": ["a"], "min": 0},
        "date_range": {"columns": ["a"], "after": "2020-01-01"},
        "allowed_values": {"columns": ["a"], "values": ["X"]},
        "compare_fields": {"left": "a", "right": "b", "operator": "le"},
        "conditional_required": {"when": {"column": "a", "value": "X"},
                                 "then": ["b"]},
        "assignment": {"source_column": "a", "target_column": "b",
                       "mapping_source": "m"},
        "filter": {"source_column": "a", "target_column": "b",
                   "mapping_source": "m"},
    }
    assert set(minimal) == set(VALIDATION_RULE_SPECS), "a rule type has no minimal case"
    for rule_type, params in minimal.items():
        validate_validation_rules([{"rule_name": "R", "type": rule_type, "params": params}])


def test_every_declared_transform_type_accepts_a_minimal_valid_rule():
    minimal = {
        "rename_columns": {"mapping": {"a": "b"}},
        "drop_columns": {"columns": ["a"]},
        "select_columns": {"columns": ["a"]},
        "reorder_columns": {"columns": ["a"]},
        "cast": {"column": "a", "dtype": "string"},
        "explode": {"column": "a"},
        "unnest": {"column": "a"},
        "flatten_struct": {"column": "a"},
        "mapping": {"expression": "pl.col('a')", "new_col": "b"},
        "combine_columns": {"target": "t", "sources": ["a", "b"]},
        "split_column": {"source": "a", "targets": ["b", "c"]},
        "build_nested_json": {"array_paths": ["to_X"]},
        "add_row_index": {"name": "__source_row"},
        "join_reference": {"file_id": "f", "on": ["k"]},
        "derive": {"when": {"column": "a", "value": "1"},
                   "then": [{"column": "b", "value": "2"}]},
        "nest_children": {"on": ["k"], "children": [{"file_id": "f", "as": "to_X"}]},
        "add_technical_fields": {"object_id": "OBJ-1"},
    }
    assert set(minimal) == set(SCHEMA_TRANSFORM_RULE_SPECS), "a rule type has no minimal case"
    for rule_type, params in minimal.items():
        validate_schema_transform_rules([{"type": rule_type, "params": params}])


# ------------------------------------------------------------- HTTP surface


def _client(module, prefix):
    app = FastAPI()
    app.include_router(module.router, prefix=prefix)
    app.state.container = {}
    return TestClient(app)


def test_validation_endpoint_returns_422_for_an_unrunnable_rule():
    client = _client(data_validation_controller, "/api/v1/validation")

    response = client.post("/api/v1/validation", json={
        "file_id": "f", "rules": [{"rule_name": "R", "type": "regex_match", "params": {}}]})

    assert response.status_code == 422
    assert "unknown rule type" in str(response.json())


def test_validation_endpoint_returns_422_for_a_negative_header_row():
    client = _client(data_validation_controller, "/api/v1/validation")

    response = client.post("/api/v1/validation", json={
        "file_id": "f", "header_row": -1,
        "rules": [{"rule_name": "R", "type": "required", "params": {"columns": ["a"]}}]})

    assert response.status_code == 422
    assert "0-based row index" in str(response.json())


def test_schema_transform_endpoint_returns_422_for_a_bad_dtype():
    client = _client(schema_transform_controller, "/api/v1/schema-transform")

    response = client.post("/api/v1/schema-transform", json={
        "source_file_id": "f",
        "rules": [{"type": "cast", "params": {"column": "a", "dtype": "int32ish"}}]})

    assert response.status_code == 422
    assert "dtype" in str(response.json())


def test_bundle_endpoint_returns_422_for_an_unrunnable_validation_rule():
    client = _client(bundle_controller, "/api/v1/bundle")

    response = client.post("/api/v1/bundle/transform-validate", json={
        "source_file_id": "wb",
        "tables": [{"name": "A",
                    "validation_rules": [{"rule_name": "R", "type": "nope", "params": {}}]}]})

    assert response.status_code == 422
    assert "unknown rule type" in str(response.json())


def test_bundle_rejects_a_forward_reference_to_a_table_not_yet_produced():
    """A join_reference resolved against a table that runs later silently no-ops."""
    client = _client(bundle_controller, "/api/v1/bundle")

    response = client.post("/api/v1/bundle/transform-validate", json={
        "source_file_id": "wb",
        "tables": [
            {"name": "Child", "rules": [{"type": "join_reference",
                                         "params": {"table": "Parent", "on": ["k"]}}]},
            {"name": "Parent"},
        ]})

    assert response.status_code == 422
    assert "not produced before it" in str(response.json())


def test_bundle_rejects_duplicate_table_names():
    client = _client(bundle_controller, "/api/v1/bundle")

    response = client.post("/api/v1/bundle/transform-validate", json={
        "source_file_id": "wb", "tables": [{"name": "A"}, {"name": "A"}]})

    assert response.status_code == 422
    assert "unique" in str(response.json())


def test_bundle_rejects_an_out_of_order_dependency():
    client = _client(bundle_controller, "/api/v1/bundle")

    response = client.post("/api/v1/bundle/transform-validate", json={
        "source_file_id": "wb",
        "tables": [{"name": "Child", "depends_on": ["Parent"]}, {"name": "Parent"}]})

    assert response.status_code == 422
    assert "order 'tables' so dependencies come first" in str(response.json())


def test_bundle_rejects_both_validation_rules_and_a_rule_set_id():
    """Only one of them is honoured downstream; asking for both hides which."""
    client = _client(bundle_controller, "/api/v1/bundle")

    response = client.post("/api/v1/bundle/transform-validate", json={
        "source_file_id": "wb",
        "tables": [{"name": "A",
                    "validation_rules": [{"rule_name": "R", "type": "required",
                                          "params": {"columns": ["a"]}}],
                    "validation_rule_set_id": "rs-1"}]})

    assert response.status_code == 422
    assert "not both" in str(response.json())


def test_bundle_rejects_a_blank_table_name():
    client = _client(bundle_controller, "/api/v1/bundle")

    response = client.post("/api/v1/bundle/transform-validate", json={
        "source_file_id": "wb", "tables": [{"name": "   "}]})

    assert response.status_code == 422
    assert "blank" in str(response.json())


def test_bundle_rejects_an_unknown_result_mode():
    client = _client(bundle_controller, "/api/v1/bundle")

    response = client.post("/api/v1/bundle/transform-validate", json={
        "source_file_id": "wb", "tables": [{"name": "A", "result_mode": "everything"}]})

    assert response.status_code == 422
    assert "result_mode" in str(response.json())


def test_a_correct_bundle_body_still_passes_the_stricter_boundary():
    """The guard must not have made the documented payload unusable."""
    client = _client(bundle_controller, "/api/v1/bundle")

    response = client.post("/api/v1/bundle/transform-validate", json={
        "source_file_id": "wb", "default_header_row": 2,
        "tables": [
            {"name": "ArticleMaster", "sheet_names": ["ArticleMaster"], "header_row": 2,
             "rules": [{"type": "select_columns", "params": {"columns": ["a"]}},
                       {"type": "cast", "params": {"columns": ["a"], "dtype": "decimal"}}],
             "validation_rules": [{"rule_name": "LENGTH_40", "type": "expression",
                                   "params": {"expression": "pl.col('a').str.len_chars() <= 40"}}]},
            {"name": "ARTMasterPurchasing", "depends_on": ["ArticleMaster"],
             "rules": [{"type": "join_reference",
                        "params": {"table": "ArticleMaster", "on": ["itemid"],
                                   "columns": ["merchandiseCategory"], "prefix": "_ref_"}},
                       {"type": "derive",
                        "params": {"when": {"column": "_ref_merchandiseCategory",
                                            "value": "GROCERY"},
                                   "then": [{"column": "purchasingInfoRecord",
                                             "value": "PC"}]}}]},
        ]})

    # 500 = it got past validation and reached the unwired container, which is
    # what we want to prove here.
    assert response.status_code == 500
    assert "not wired" in response.json()["detail"]
