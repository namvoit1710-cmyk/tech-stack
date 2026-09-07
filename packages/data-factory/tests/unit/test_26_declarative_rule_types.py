"""The seven declarative rule types.

Every one of these could be written as an `expression`, and before this they had
to be. The point is not new power — it is that `{"type": "length", "max": 40}`
is a number in a field that a form can render and the boundary can check, while
`pl.col('baseUnit').str.len_chars() <= 40` is a program, and changing 40 to 50
means editing code.

So the tests care about two things beyond "does it flag the right rows": that a
null is never a violation (absence is `required`'s job, and a missing value
should fail one rule rather than every rule naming the column), and that a
malformed rule is refused rather than quietly doing nothing.
"""

import polars as pl
import pytest

from app.layer1_domain.entities.validation import ValidationRule
from app.layer3_adapters.controllers.restful.v1.dtos.rule_spec import (
    VALIDATION_RULE_SPECS,
    RuleSpecError,
    validate_validation_rules,
)
from app.layer4_frameworks.providers.validation.rule_handlers import (
    RuleFactory,
    RuleParamError,
)

#: Handled by the validator provider itself, not by a RuleFactory handler.
HANDLED_ELSEWHERE = {"unique", "set_unique", "reference_lookup", "assignment",
                     # a Filter Rule is the same source -> target question,
                     # so it runs in the assignment pass rather than a handler
                     "filter",
                     # a composite AND of exact and fuzzy components - a
                     # global rule, like the two uniques it generalises
                     "composite_unique",
                     # semantic_unique compares embeddings in the database, so it
                     # needs a round trip before it has anything to express - the
                     # same reason reference_lookup is not a row handler either
                     "semantic_unique"}


@pytest.fixture
def frame():
    return pl.DataFrame({
        "id": ["r1", "r2", "r3", "r4"],
        "code": ["AB", "TOOLONGVALUE", None, "XY"],
        "email": ["a@b.com", "nope", None, "c@d.org"],
        "qty": ["5", "150", None, "not-a-number"],
        "when": ["2025-06-23", "2019-01-01", None, "garbage"],
        "status": ["ACTIVE", "retired", None, "ACTIVE"],
        "valid_from": ["2025-01-01", "2025-05-01", "2025-01-01", None],
        "valid_to": ["2025-12-31", "2025-02-01", None, "2025-12-31"],
        "category": ["FOOD", "FOOD", "GROCERY", "FOOD"],
        "temperature": ["01", None, None, "  "],
    })


def _rule(type_, **params):
    return ValidationRule(rule_name=f"{type_.upper()}_RULE", type=type_,
                          error_message=f"{type_} violated", params=params)


def _flagged(frame, rule):
    """The ids of rows this rule flags."""
    handler = RuleFactory().get_handler(rule.type)
    assert handler is not None, f"no handler for {rule.type}"
    out = frame.with_columns(handler.parse_rule(rule, "err_0"))
    error_columns = [c for c in out.columns if c.startswith("err_0")]
    return [row["id"] for row in out.to_dicts()
            if any(row[c] is not None for c in error_columns)]


def _first_violation(frame, rule):
    handler = RuleFactory().get_handler(rule.type)
    out = frame.with_columns(handler.parse_rule(rule, "err_0"))
    error_columns = [c for c in out.columns if c.startswith("err_0")]
    for row in out.to_dicts():
        for column in error_columns:
            if row[column] is not None:
                return row[column]
    return None


# ------------------------------------------------------------------- length

def test_length_flags_only_what_is_too_long(frame):
    assert _flagged(frame, _rule("length", columns=["code"], max=4)) == ["r2"]


def test_length_can_bound_both_ends(frame):
    """"AB" and "XY" are both under min; "TOOLONGVALUE" is over max."""
    assert _flagged(frame, _rule("length", columns=["code"], min=3, max=4)) == [
        "r1", "r2", "r4",
    ]


def test_length_needs_a_bound(frame):
    with pytest.raises(RuleParamError, match="needs min, max, or both"):
        _flagged(frame, _rule("length", columns=["code"]))


# ------------------------------------------------------------------ pattern

def test_pattern_flags_what_does_not_match(frame):
    rule = _rule("pattern", columns=["email"], pattern=r"^[^@]+@[^@]+\.[a-z]{2,}$")
    assert _flagged(frame, rule) == ["r2"]


def test_full_match_anchors_the_pattern(frame):
    """Unanchored, 'AB' matches inside 'TOOLONGVALUE'... it does not, but 'XY'
    matching a substring rule is the trap this option exists for."""
    loose = _rule("pattern", columns=["code"], pattern="[A-Z]{2}")
    strict = _rule("pattern", columns=["code"], pattern="[A-Z]{2}", full_match=True)

    assert _flagged(frame, loose) == []
    assert _flagged(frame, strict) == ["r2"]


def test_a_broken_regex_is_refused_not_ignored(frame):
    with pytest.raises(RuleParamError, match="not a valid regex"):
        _flagged(frame, _rule("pattern", columns=["code"], pattern="[unclosed"))


# -------------------------------------------------------------------- range

def test_range_flags_out_of_bounds_and_unparseable(frame):
    """'not-a-number' in a numeric column is a violation, not a row to skip —
    skipping it would report it as being in range."""
    assert _flagged(frame, _rule("range", columns=["qty"], min=1, max=100)) == ["r2", "r4"]


def test_range_bounds_are_inclusive_unless_told_otherwise(frame):
    assert _flagged(frame, _rule("range", columns=["qty"], min=5, max=150)) == ["r4"]
    assert _flagged(frame, _rule("range", columns=["qty"], min=5, max=150,
                                 exclusive=True)) == ["r1", "r2", "r4"]


# --------------------------------------------------------------- date_range

def test_date_range_flags_dates_outside_the_window(frame):
    rule = _rule("date_range", columns=["when"], after="2020-01-01")
    assert _flagged(frame, rule) == ["r2", "r4"]  # r4 is unparseable


def test_date_range_respects_a_custom_format():
    frame = pl.DataFrame({"id": ["r1", "r2"], "when": ["23/06/2025", "01/01/2019"]})
    rule = _rule("date_range", columns=["when"], after="01/01/2020", format="%d/%m/%Y")

    assert _flagged(frame, rule) == ["r2"]


# ----------------------------------------------------------- allowed_values

def test_allowed_values_flags_what_is_not_in_the_set(frame):
    assert _flagged(frame, _rule("allowed_values", columns=["status"],
                                 values=["ACTIVE", "DRAFT"])) == ["r2"]


def test_allowed_values_can_ignore_case(frame):
    rule = _rule("allowed_values", columns=["status"], values=["ACTIVE", "RETIRED"],
                 case_sensitive=False)
    assert _flagged(frame, rule) == []


def test_allowed_values_needs_a_non_empty_list(frame):
    with pytest.raises(RuleParamError, match="non-empty list"):
        _flagged(frame, _rule("allowed_values", columns=["status"], values=[]))


# ---------------------------------------------------------- compare_fields

def test_compare_fields_flags_the_row_where_the_order_is_wrong(frame):
    """valid_to must not precede valid_from. r3 and r4 have a null side and are
    a `required` problem, not a comparison failure."""
    rule = _rule("compare_fields", left="valid_from", right="valid_to", operator="le")

    assert _flagged(frame, rule) == ["r2"]


def test_compare_fields_can_compare_numerically(frame):
    numbers = pl.DataFrame({"id": ["r1", "r2"], "a": ["9", "100"], "b": ["10", "20"]})
    text = _rule("compare_fields", left="a", right="b", operator="le")
    numeric = _rule("compare_fields", left="a", right="b", operator="le", numeric=True)

    # As text, "9" > "100"; as numbers it is not. Both answers are defensible,
    # which is exactly why this is a flag and not a guess.
    assert _flagged(numbers, text) == ["r1"]
    assert _flagged(numbers, numeric) == ["r2"]


def test_compare_fields_rejects_an_unknown_operator(frame):
    with pytest.raises(RuleParamError, match="unknown operator"):
        _flagged(frame, _rule("compare_fields", left="valid_from", right="valid_to",
                              operator="before"))


# --------------------------------------------------- conditional_required

def test_conditional_required_only_fires_when_the_condition_holds(frame):
    """FOOD needs a temperature. r3 is GROCERY and is left alone even though its
    temperature is null; r4's whitespace counts as blank."""
    rule = _rule("conditional_required",
                 when={"column": "category", "operator": "eq", "value": "FOOD"},
                 then=["temperature"])

    assert _flagged(frame, rule) == ["r2", "r4"]


def test_conditional_required_compares_as_text_so_leading_zeros_survive():
    frame = pl.DataFrame({"id": ["r1", "r2"], "code": ["01", "1"],
                          "detail": [None, None]})
    rule = _rule("conditional_required",
                 when={"column": "code", "value": "01"}, then=["detail"])

    assert _flagged(frame, rule) == ["r1"]


def test_conditional_required_needs_a_condition_column(frame):
    with pytest.raises(RuleParamError, match="names no column"):
        _flagged(frame, _rule("conditional_required", when={"value": "FOOD"},
                              then=["temperature"]))


# ------------------------------------------------------------ shared shape

@pytest.mark.parametrize("rule", [
    _rule("length", columns=["code"], max=4),
    _rule("pattern", columns=["email"], pattern="^x$"),
    _rule("range", columns=["qty"], max=10),
    _rule("date_range", columns=["when"], after="2020-01-01"),
    _rule("allowed_values", columns=["status"], values=["ACTIVE"]),
    _rule("compare_fields", left="valid_from", right="valid_to", operator="gt"),
])
def test_a_violation_names_the_rule_the_field_and_the_value(frame, rule):
    violation = _first_violation(frame, rule)

    assert violation is not None
    assert violation["rule"] == rule.rule_name
    assert violation["message"] == rule.error_message
    assert violation["field"] in frame.columns


@pytest.mark.parametrize("rule", [
    _rule("length", columns=["code"], max=4),
    _rule("pattern", columns=["code"], pattern="^[A-Z]{2}$"),
    _rule("range", columns=["qty"], min=1, max=100),
    _rule("date_range", columns=["when"], after="2020-01-01"),
    _rule("allowed_values", columns=["status"], values=["ACTIVE"]),
    _rule("conditional_required",
          when={"column": "category", "value": "FOOD"}, then=["temperature"]),
])
def test_a_null_is_never_a_violation(frame, rule):
    """r3 is null in every column these rules touch. Absence is `required`'s job;
    if every rule also fired on it, one blank cell would produce six errors."""
    assert "r3" not in _flagged(frame, rule)


# ------------------------------------------------------- boundary agreement

def test_every_declared_type_can_actually_be_run():
    """The spec registry and the engine have to agree. A type declared but not
    implemented would pass the boundary and then be skipped — which is the
    silent-pass this whole area was built to remove."""
    factory = RuleFactory()
    for rule_type in VALIDATION_RULE_SPECS:
        if rule_type in HANDLED_ELSEWHERE:
            continue
        assert factory.get_handler(rule_type) is not None, (
            f"'{rule_type}' is declared in VALIDATION_RULE_SPECS but no handler "
            "will run it"
        )


def test_every_handler_is_declared_at_the_boundary():
    """The other direction: a handler with no spec is unreachable over HTTP,
    because the DTO rejects unknown types."""
    for rule_type in RuleFactory().handlers:
        assert rule_type in VALIDATION_RULE_SPECS, (
            f"'{rule_type}' has a handler but is not declared, so the boundary "
            "will 422 it"
        )


@pytest.mark.parametrize("payload,expected", [
    ({"type": "length", "params": {"columns": ["a"]}}, "needs one of min, max"),
    ({"type": "length", "params": {"columns": ["a"], "max": -1}}, "max"),
    ({"type": "pattern", "params": {"columns": ["a"]}}, "missing required param 'pattern'"),
    ({"type": "range", "params": {"columns": ["a"], "minimum": 1}}, "unknown param"),
    ({"type": "allowed_values", "params": {"columns": ["a"]}}, "missing required param 'values'"),
    ({"type": "compare_fields",
      "params": {"left": "a", "right": "b", "operator": "before"}}, "operator"),
    ({"type": "conditional_required", "params": {"then": ["a"]}}, "missing required param 'when'"),
])
def test_a_malformed_declarative_rule_is_refused_at_the_boundary(payload, expected):
    with pytest.raises(RuleSpecError, match=expected):
        validate_validation_rules([payload])


def test_the_documented_length_rule_is_accepted():
    accepted = validate_validation_rules(
        [{"rule_name": "LENGTH_40", "type": "length",
          "params": {"columns": ["baseUnit"], "max": 40}}]
    )

    assert accepted[0]["params"]["max"] == 40
