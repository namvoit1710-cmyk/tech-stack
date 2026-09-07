"""`composite_unique` — an AND of fields, each matched exactly or loosely.

This is the shape SMDG's duplication config actually uses. A `DuplicationCheck`
group ANDs its fields and each field carries its own `isFuzzySearch`, so a real
rule off a live config container reads:

    organizationBPName1  fuzzy
AND streetName           fuzzy
AND cityName, country, postalCode, region   exact

`set_unique` compares a composite key exactly and `fuzzy_unique` compares one
column loosely; neither expresses the mixture. Six of the fifteen configured
groups on the landscape inspected are exactly this, so the rules were being
dropped rather than run.
"""
import logging

import polars as pl
import pytest

from app.layer3_adapters.controllers.restful.v1.dtos.rule_spec import (
    RuleSpecError,
    validate_validation_rules,
)
from app.layer4_frameworks.providers.validation.polars_validator_provider import (
    PolarsValidatorProvider,
)


class _Rule:
    def __init__(self, columns, name="DUP_G1"):
        self.rule_name = name
        self.type = "composite_unique"
        self.error_message = "another row identifies the same object"
        self.params = {"columns": columns}
        self.field_paths = {}


def _flagged(frame, rule):
    provider = PolarsValidatorProvider(logging.getLogger("t"), storage=None)
    exprs, mapping = provider._build_composite_unique(frame, rule, 0)
    if not exprs:
        return []
    out = frame.with_columns(exprs)
    hits = [c for c in out.columns if c.startswith("compuniq_")]
    return [i for i, row in enumerate(out.select(hits).iter_rows())
            if any(c is not None for c in row)]


@pytest.fixture
def frame():
    # Rows 0 and 1 are the same company typed two ways at the same address.
    # Row 2 is the same name at a DIFFERENT address - not a duplicate, because
    # the group ANDs the address in.
    return pl.DataFrame({
        "name":   ["ACME Ltd.", "acme  ltd", "ACME Ltd.", "Beta Co"],
        "street": ["1 High St.", "1 high st", "9 Other Rd", "2 Low St"],
        "city":   ["Leeds",      "Leeds",     "Leeds",     "Hull"],
    })


def test_the_and_holds_so_a_different_address_is_not_a_duplicate(frame):
    rule = _Rule([{"column": "name", "match": "fuzzy"},
                  {"column": "street", "match": "fuzzy"},
                  "city"])
    assert _flagged(frame, rule) == [0, 1]


def test_all_exact_behaves_like_set_unique(frame):
    assert _flagged(frame, _Rule(["name", "city"])) == [0, 2]


def test_a_bare_string_component_means_exact(frame):
    # The short form has to keep meaning the strict thing; defaulting a component
    # to fuzzy would report duplicates the template never asked about.
    assert _flagged(frame, _Rule(["name"])) == [0, 2]


def test_a_row_missing_a_component_is_not_a_duplicate():
    """Two half-empty rows are not evidence of the same object - absence is
    `required`'s business, and matching on it would flag every incomplete row."""
    data = pl.DataFrame({"name": ["ACME", "ACME"], "street": [None, ""]})
    assert _flagged(data, _Rule(["name", "street"])) == []


def test_fuzzy_collapses_case_spacing_and_punctuation():
    data = pl.DataFrame({"name": ["ACME-Ltd.", "acme ltd"], "city": ["Leeds", "Leeds"]})
    assert _flagged(data, _Rule([{"column": "name", "match": "fuzzy"}, "city"])) == [0, 1]
    # ...and the same pair is NOT an exact duplicate.
    assert _flagged(data, _Rule(["name", "city"])) == []


def test_components_cannot_be_confused_across_the_separator():
    """'AB' + 'C' must not collide with 'A' + 'BC'. Without a separator that
    ordinary data cannot contain, a composite key forges its own boundaries."""
    data = pl.DataFrame({"a": ["AB", "A"], "b": ["C", "BC"]})
    assert _flagged(data, _Rule(["a", "b"])) == []


def test_a_column_the_file_lacks_is_an_error_not_a_skip(frame):
    with pytest.raises(ValueError, match="not in the file"):
        _flagged(frame, _Rule(["name", "nope"]))


def test_a_semantic_component_without_resolved_clusters_is_an_error(frame):
    """Falling back to an exact key would quietly turn a semantic match into a
    strict one and report fewer duplicates than the template asks for."""
    with pytest.raises(ValueError, match="no resolved clusters"):
        _flagged(frame, _Rule([{"column": "name", "match": "semantic"}]))


# ------------------------------------------------------------- at the edge

def test_boundary_accepts_the_real_ac37_shape():
    validate_validation_rules([{
        "type": "composite_unique",
        "params": {"columns": [
            {"column": "BusinessPartner.organizationBPName1", "match": "fuzzy"},
            {"column": "BusinessPartnerAddress.streetName", "match": "fuzzy"},
            "BusinessPartnerAddress.cityName",
            "BusinessPartnerAddress.country",
            "BusinessPartnerAddress.postalCode",
            "BusinessPartnerAddress.region"]},
    }])


def test_boundary_rejects_an_unknown_match_mode():
    with pytest.raises(RuleSpecError, match="must be exact or fuzzy"):
        validate_validation_rules([{
            "type": "composite_unique",
            "params": {"columns": [{"column": "a", "match": "roughly"}]}}])


def test_boundary_rejects_a_component_with_no_column():
    with pytest.raises(RuleSpecError, match="must be a column name"):
        validate_validation_rules([{
            "type": "composite_unique",
            "params": {"columns": [{"match": "fuzzy"}]}}])
