"""One `when` grammar, shared by `derive` and `conditional_required`.

These tests exist because the two rule types used to disagree. `derive` took
fourteen upper-case operators and `{match, conditions}`; `conditional_required`
took six lower-case ones and a single condition. The consequence was not
cosmetic: SMDG's `MandatoryRule` is a `whenLogicalExpression` over a SET of
SOURCE conditions, so the mass-upload rule shape could not be written as a
`conditional_required` at all.

The thing most worth guarding here is the DIRECTION of a mistake. A mandatory
rule whose trigger is silently narrowed fires less often, so the run comes back
with fewer violations - a failure that reads exactly like success. Every
malformed condition below must therefore raise, not warn.
"""
import polars as pl
import pytest

from app.layer3_adapters.controllers.restful.v1.dtos.rule_spec import (
    RuleSpecError,
    validate_validation_rules,
)
from app.layer4_frameworks.providers.conditions import (
    build_condition,
    canonical_operator,
    single_condition,
)
from app.layer4_frameworks.providers.validation.rule_handlers import (
    ConditionalRequiredRuleHandler,
    RuleParamError,
)


class _Rule:
    def __init__(self, **params):
        self.rule_name = "R1"
        self.type = "conditional_required"
        self.error_message = "required"
        self.params = params
        self.field_paths = {}


@pytest.fixture
def frame():
    return pl.DataFrame({
        "materialType": ["VERP", "VERP", "ROH", "VERP"],
        "baseUnit":     ["EA",   "",     "EA",  "EA"],
        "grossWeight":  ["",     "",     "",    "12"],
        "qty":          ["9",    "10",   "100", "3"],
    })


def _fired(frame, rule):
    """The rows a conditional_required rule flags, as a list of row indexes."""
    handler = ConditionalRequiredRuleHandler()
    out = frame.with_columns(handler.parse_rule(rule, "err"))
    hits = [c for c in out.columns if c.startswith("err")]
    return [
        index
        for index, row in enumerate(out.select(hits).iter_rows())
        if any(cell is not None for cell in row)
    ]


# ------------------------------------------------------ the grammar itself

def test_lower_case_aliases_still_resolve():
    # Every conditional_required payload written before the merge used these.
    assert canonical_operator("eq") == "EQUAL"
    assert canonical_operator("ne") == "NOT_EQUAL"


def test_upper_case_operators_resolve():
    assert canonical_operator("IS_NOT_EMPTY") == "IS_NOT_EMPTY"
    assert canonical_operator("starts_with") == "STARTS_WITH"


def test_missing_operator_defaults_to_equal():
    assert canonical_operator(None) == "EQUAL"
    assert canonical_operator("") == "EQUAL"


def test_unknown_operator_is_none_not_a_guess():
    # It used to fall through to EQUAL with a warning, which turns a typo into a
    # rule that runs and reports the wrong thing.
    assert canonical_operator("approximately") is None


def test_ordering_operators_compare_as_numbers(frame):
    # '9' vs '10': the case that makes text ordering wrong. GT 5 must catch both
    # 9 and 10 and 100, not just the ones that happen to sort right.
    expr = single_condition({"column": "qty", "operator": "gt", "value": 5})
    assert frame.select(expr).to_series().to_list() == [True, True, True, False]


def test_equality_still_compares_as_text_so_leading_zeros_survive():
    data = pl.DataFrame({"code": ["01", "1"]})
    expr = single_condition({"column": "code", "operator": "eq", "value": "01"})
    assert data.select(expr).to_series().to_list() == [True, False]


def test_match_all_is_the_default(frame):
    expr = build_condition({"conditions": [
        {"column": "materialType", "value": "VERP"},
        {"column": "baseUnit", "operator": "IS_NOT_EMPTY"},
    ]})
    assert frame.select(expr).to_series().to_list() == [True, False, False, True]


def test_match_any_ors_them(frame):
    expr = build_condition({"match": "ANY", "conditions": [
        {"column": "materialType", "value": "ROH"},
        {"column": "baseUnit", "operator": "IS_EMPTY"},
    ]})
    assert frame.select(expr).to_series().to_list() == [False, True, True, False]


def test_an_unknown_column_is_dropped_only_when_the_caller_says_which_exist():
    # The transformer passes df.columns so a derivation with an absent trigger is
    # skipped. The validator passes nothing, so the reference survives and polars
    # reports it -- there, a rule naming a missing column IS the error.
    condition = {"column": "nope", "value": "X"}
    assert single_condition(condition, known_columns=["a", "b"]) is None
    assert single_condition(condition) is not None


# ------------------------------------------- conditional_required end to end

def test_multi_condition_mandatory_rule_now_expressible(frame):
    """WHEN materialType = VERP AND baseUnit is filled, THEN grossWeight is
    mandatory. This is the SMDG MandatoryRule shape."""
    rule = _Rule(
        when={"match": "ALL", "conditions": [
            {"column": "materialType", "operator": "EQUAL", "value": "VERP"},
            {"column": "baseUnit", "operator": "IS_NOT_EMPTY"},
        ]},
        then=["grossWeight"],
    )
    # Row 0 triggers and grossWeight is blank -> flagged.
    # Row 1 baseUnit empty, row 2 wrong type -> not triggered.
    # Row 3 triggers but grossWeight is filled -> not flagged.
    assert _fired(frame, rule) == [0]


def test_single_condition_shape_is_unchanged(frame):
    rule = _Rule(when={"column": "materialType", "value": "VERP"},
                 then=["grossWeight"])
    assert _fired(frame, rule) == [0, 1]


def test_a_typod_operator_raises_rather_than_narrowing_the_trigger(frame):
    rule = _Rule(when={"column": "materialType", "operator": "EQUALS",
                       "value": "VERP"},
                 then=["grossWeight"])
    with pytest.raises(RuleParamError, match="unsupported operator"):
        _fired(frame, rule)


def test_one_bad_condition_among_good_ones_still_raises(frame):
    """The dangerous case: two conditions ANDed, one silently dropped. The
    surviving trigger is WIDER, so the rule reports violations the template
    never asked for -- or, with ANY, narrower. Either way it is not the rule."""
    rule = _Rule(
        when={"conditions": [
            {"column": "materialType", "value": "VERP"},
            {"column": "baseUnit", "operator": "roughly", "value": "EA"},
        ]},
        then=["grossWeight"],
    )
    with pytest.raises(RuleParamError, match="unsupported operator"):
        _fired(frame, rule)


def test_when_with_no_usable_condition_raises(frame):
    rule = _Rule(when={"conditions": []}, then=["grossWeight"])
    with pytest.raises(RuleParamError, match="no usable condition"):
        _fired(frame, rule)


# -------------------------------------------------------------- at the edge

def test_boundary_accepts_a_multi_condition_when():
    validate_validation_rules([{
        "type": "conditional_required",
        "params": {"when": {"match": "ANY", "conditions": [
            {"column": "a", "operator": "IN", "value": ["X", "Y"]},
            {"column": "b", "operator": "IS_EMPTY"},
        ]}, "then": ["c"]},
    }])


def test_boundary_still_accepts_the_lower_case_spelling():
    validate_validation_rules([{
        "type": "conditional_required",
        "params": {"when": {"column": "a", "operator": "eq", "value": "X"},
                   "then": ["c"]},
    }])


def test_boundary_rejects_an_unknown_operator():
    with pytest.raises(RuleSpecError, match="unknown operator"):
        validate_validation_rules([{
            "type": "conditional_required",
            "params": {"when": {"column": "a", "operator": "sortof", "value": "X"},
                       "then": ["c"]},
        }])


def test_boundary_rejects_a_bad_match_keyword():
    with pytest.raises(RuleSpecError, match="must be ALL or ANY"):
        validate_validation_rules([{
            "type": "conditional_required",
            "params": {"when": {"match": "EITHER", "conditions": [
                {"column": "a", "value": "X"}]}, "then": ["c"]},
        }])
