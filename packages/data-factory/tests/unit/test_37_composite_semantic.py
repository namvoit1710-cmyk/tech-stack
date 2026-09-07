"""The dev duplication rule for MM_VERP_01_AI, as written.

    Manufacturer Part Number (Product)              exact
AND Manufacturer Name        (Product)              exact
AND Product Description      (ProductDescription)   semantic

SMDG cannot store this - `isFuzzySearch` is a boolean, so there is no third
match mode - and DF could not express it either: `composite_unique` and
`semantic_unique` each run, but as two rules they OR rather than AND, which
reports far more than the template asks for.

A semantic component resolves to a CLUSTER key first, and then joins the same
tuple comparison the exact and fuzzy components already use.
"""
import logging

import polars as pl
import pytest

from app.layer4_frameworks.providers.validation.polars_validator_provider import (
    PolarsValidatorProvider,
)
from app.layer4_frameworks.providers.validation.semantic_match import (
    SemanticMatcher,
    SemanticSpec,
)

TIE_A = 'TIE,CABLE,LOCKING,7.31" LG X 0.184"'
TIE_B = 'tie,cabl,   self-LOCKING,  7.31 LG X 0.184'


class FakeDB:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def __call__(self, sql, params):
        self.calls.append((sql, list(params)))
        return self.rows


class _Rule:
    def __init__(self, columns):
        self.rule_name = "MM_VERP_01_AI_DUP_G1"
        self.type = "composite_unique"
        self.error_message = "this product already exists"
        self.params = {"columns": columns}
        self.field_paths = {}


def _flagged(frame, rule, db):
    provider = PolarsValidatorProvider(logging.getLogger("t"), storage=None)
    provider.semantic_executor = db
    exprs, _ = provider._composite_with_semantic(rule, 0, frame)
    if not exprs:
        return []
    out = frame.with_columns(exprs)
    hits = [c for c in out.columns if c.startswith("compuniq_")]
    return [i for i, row in enumerate(out.select(hits).iter_rows())
            if any(c is not None for c in row)]


BA_RULE = [
    {"column": "mfrPartNumber", "match": "exact"},
    {"column": "mfrName", "match": "exact"},
    {"column": "description", "match": "semantic", "threshold": 0.85},
]


def test_the_and_holds_so_a_different_manufacturer_is_not_a_duplicate():
    """Rows 0 and 1 are the same tie described two ways, same manufacturer.
    Row 2 is described the same as row 0 but by a DIFFERENT manufacturer - the
    AND is what keeps it out."""
    frame = pl.DataFrame({
        "mfrPartNumber": ["CT-731", "CT-731", "CT-731", "ZZ-999"],
        "mfrName":       ["Acme",   "Acme",   "Other",  "Acme"],
        "description":   [TIE_A,    TIE_B,    TIE_A,    "SPRING, 6in"],
    })
    db = FakeDB([(TIE_A, TIE_B, 0.8960)])
    assert _flagged(frame, _Rule(BA_RULE), db) == [0, 1]


def test_matching_descriptions_alone_is_not_enough():
    frame = pl.DataFrame({
        "mfrPartNumber": ["AAA-1", "BBB-2"],
        "mfrName":       ["Acme",  "Beta"],
        "description":   [TIE_A,   TIE_B],
    })
    db = FakeDB([(TIE_A, TIE_B, 0.8960)])
    assert _flagged(frame, _Rule(BA_RULE), db) == []


def test_the_semantic_half_is_what_finds_the_pair():
    """Without it the two spellings are different strings and nothing matches -
    which is precisely what fuzzy_unique does on this fixture."""
    frame = pl.DataFrame({
        "mfrPartNumber": ["CT-731", "CT-731"],
        "mfrName":       ["Acme",   "Acme"],
        "description":   [TIE_A,    TIE_B],
    })
    assert _flagged(frame, _Rule(BA_RULE), FakeDB([])) == []          # no match found
    assert _flagged(frame, _Rule(BA_RULE), FakeDB([(TIE_A, TIE_B, 0.8960)])) == [0, 1]


def test_the_empty_manufacturer_case_from_the_real_workbook():
    """In the actual file both manufacturer fields are empty in every row, so
    SMDG compiles them to IS NULL and they stop discriminating. Here they are
    simply missing components, which makes the row not a duplicate at all - a
    STRICTER answer than production's, and the difference is worth knowing."""
    frame = pl.DataFrame({
        "mfrPartNumber": [None, None],
        "mfrName":       ["",   ""],
        "description":   [TIE_A, TIE_B],
    })
    assert _flagged(frame, _Rule(BA_RULE), FakeDB([(TIE_A, TIE_B, 0.8960)])) == []


def test_each_semantic_component_carries_its_own_threshold():
    frame = pl.DataFrame({"a": ["one thing", "two thing"],
                          "description": [TIE_A, TIE_B]})
    db = FakeDB([(TIE_A, TIE_B, 0.8960)])
    _flagged(frame, _Rule([
        "a", {"column": "description", "match": "semantic", "threshold": 0.93}]), db)
    assert db.calls[0][1][-1] == 0.93


# ------------------------------------------------------------- clustering

def test_a_value_with_no_match_keys_on_itself():
    spec = SemanticSpec.parse({"columns": ["d"]})
    clusters = SemanticMatcher(spec, FakeDB([])).clusters(["alpha one", "beta two"])
    assert clusters == {"alpha one": "alpha one", "beta two": "beta two"}


def test_a_matched_pair_shares_one_canonical_name():
    spec = SemanticSpec.parse({"columns": ["d"]})
    clusters = SemanticMatcher(spec, FakeDB([(TIE_A, TIE_B, 0.896)])).clusters([TIE_A, TIE_B])
    assert clusters[TIE_A] == clusters[TIE_B]


def test_the_canonical_name_does_not_depend_on_row_order():
    spec = SemanticSpec.parse({"columns": ["d"]})
    a = SemanticMatcher(spec, FakeDB([(TIE_A, TIE_B, 0.896)])).clusters([TIE_A, TIE_B])
    b = SemanticMatcher(spec, FakeDB([(TIE_A, TIE_B, 0.896)])).clusters([TIE_B, TIE_A])
    assert set(a.values()) == set(b.values())


def test_chained_matches_collapse_into_one_cluster():
    """Cosine is not transitive: a~b and b~c does not give a~c. Joining them can
    merge things never compared favourably. That is deliberate - it over-reports,
    and for a duplication check over-reporting is the recoverable direction."""
    spec = SemanticSpec.parse({"columns": ["d"]})
    db = FakeDB([("alpha one", "beta two", 0.9), ("beta two", "gamma three", 0.9)])
    clusters = SemanticMatcher(spec, db).clusters(["alpha one", "beta two", "gamma three"])
    assert len(set(clusters.values())) == 1
