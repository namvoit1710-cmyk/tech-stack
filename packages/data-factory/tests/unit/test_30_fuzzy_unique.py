"""`fuzzy_unique` finds the duplicates `unique` cannot see.

The rule exists because master data is typed by people: ' ACME Ltd.' and 'acme ltd'
are one vendor entered twice, and an exact comparison reports neither of them. Each
case here pins one difference the rule is meant to ignore, and - just as important -
one it must NOT ignore, because a near-duplicate check that collapses too much flags
every row and teaches nothing.
"""

import polars as pl
import pytest

from app.layer3_adapters.controllers.restful.v1.dtos.rule_spec import (
    RuleSpecError,
    validate_validation_rules,
)
from app.layer4_frameworks.providers.validation.rule_handlers import (
    RuleFactory,
    RuleParamError,
)


class _Rule:
    """The shape the handlers read - the engine's own rule object, minus the ORM."""

    def __init__(self, rule_type, params, name="R", message="near-duplicate"):
        self.type = rule_type
        self.params = params
        self.rule_name = name
        self.error_message = message
        self.field_paths = {}


def _flagged(values, method="normalized", column="NAME"):
    """Which rows the rule reports, as a list of bools."""
    rule = _Rule("fuzzy_unique", {"columns": [column], "method": method})
    handler = RuleFactory().get_handler("fuzzy_unique")
    exprs = handler.parse_rule(rule, "v")
    df = pl.DataFrame({column: values})
    out = df.select(exprs)
    return [row is not None for row in out.to_series(0).to_list()]


# ------------------------------------------------------------- what it catches

def test_leading_whitespace_is_the_same_vendor():
    assert _flagged(["ACME Ltd", " ACME Ltd", "Beta"]) == [True, True, False]


def test_case_and_punctuation_are_the_same_vendor():
    assert _flagged(["ACME-Ltd.", "acme ltd", "Beta"]) == [True, True, False]


def test_digits_method_ignores_phone_formatting():
    got = _flagged(["+49 (0)30 1234", "4903012 34", "49301111"], method="digits")
    assert got == [True, True, False]


def test_trimmed_is_gentler_than_normalized():
    """'ACME-Ltd.' and 'acme ltd' differ by punctuation, which `trimmed` keeps."""
    assert _flagged(["ACME-Ltd.", "acme ltd"], method="trimmed") == [False, False]
    assert _flagged(["ACME-Ltd.", "acme ltd"], method="normalized") == [True, True]


# ------------------------------------------------------- what it must not catch

def test_genuinely_different_values_are_left_alone():
    assert _flagged(["ACME", "Beta", "Gamma"]) == [False, False, False]


def test_blanks_are_not_near_duplicates_of_each_other():
    """Blanks belong to `required`. Flagging them here would report every
    incomplete row a second time under a rule that is not about completeness."""
    assert _flagged(["", "", None, "ACME"]) == [False, False, False, False]


def test_punctuation_only_values_do_not_collapse_into_one_group():
    """'---' and '...' both normalise to an empty key; treating that as a match
    would flag every placeholder cell as a duplicate of every other."""
    assert _flagged(["---", "...", "ACME"]) == [False, False, False]


# --------------------------------------------------------------- the boundary

def test_the_api_accepts_the_rule():
    validate_validation_rules([
        {"rule_name": "Near-duplicate vendor", "type": "fuzzy_unique",
         "params": {"columns": ["NAME"], "method": "normalized"}}
    ])


def test_the_api_refuses_an_unknown_method_and_names_the_options():
    with pytest.raises(RuleSpecError) as caught:
        validate_validation_rules([
            {"rule_name": "R", "type": "fuzzy_unique",
             "params": {"columns": ["NAME"], "method": "soundex"}}
        ])
    message = str(caught.value)
    assert "soundex" in message
    assert "normalized" in message


def test_the_handler_refuses_an_unknown_method_too():
    """The boundary is not the only caller - df_migration_job builds rules directly."""
    handler = RuleFactory().get_handler("fuzzy_unique")
    with pytest.raises(RuleParamError) as caught:
        handler.parse_rule(_Rule("fuzzy_unique",
                                 {"columns": ["NAME"], "method": "soundex"}), "v")
    assert "normalized" in str(caught.value)


def test_method_defaults_to_normalized():
    rule = _Rule("fuzzy_unique", {"columns": ["NAME"]})
    exprs = RuleFactory().get_handler("fuzzy_unique").parse_rule(rule, "v")
    df = pl.DataFrame({"NAME": ["ACME-Ltd.", "acme ltd"]})
    assert [r is not None for r in df.select(exprs).to_series(0).to_list()] == [True, True]


# ----------------------------------------------------------- the reported shape

def test_the_violation_carries_the_offending_value():
    rule = _Rule("fuzzy_unique", {"columns": ["NAME"]}, name="Dup vendor",
                 message="near-duplicate vendor name")
    exprs = RuleFactory().get_handler("fuzzy_unique").parse_rule(rule, "v")
    df = pl.DataFrame({"NAME": ["ACME Ltd", " ACME Ltd"]})
    first = df.select(exprs).to_series(0).to_list()[0]
    assert first["rule"] == "Dup vendor"
    assert first["field"] == "NAME"
    assert first["message"] == "near-duplicate vendor name"
    assert first["value"] == "ACME Ltd"
