"""`semantic_unique` — duplicate detection by meaning.

The fixture throughout is the real one: two spellings of one cable tie that
`fuzzy_unique` reports as distinct, because `CABLE`->`CABL` plus an inserted `SELF`
produces a different blocking key. HANA scored that pair 0.8960.

The database is faked. Every test here is about which query gets built and how the
rows are reduced — the parts that are wrong quietly. Whether HANA's cosine is any
good is HANA's problem, and a test that needed a live connection would not run.
"""
import pytest

from app.layer3_adapters.controllers.restful.v1.dtos.rule_spec import (
    RuleSpecError,
    validate_validation_rules,
)
from app.layer4_frameworks.providers.validation.semantic_match import (
    DEFAULT_MODEL,
    MAX_VALUES_PER_QUERY,
    Reference,
    SemanticMatcher,
    SemanticRuleError,
    SemanticSpec,
    candidate_values,
    quote_ident,
    reference_sql,
    within_file_sql,
)

TIE_A = 'TIE,CABLE,LOCKING,7.31" LG X 0.184"'
TIE_B = 'tie,cabl,   self-LOCKING,  7.31 LG X 0.184'


class FakeDB:
    """Records what it was asked, answers what it was told to."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def __call__(self, sql, params):
        self.calls.append((sql, list(params)))
        return self.rows


# ------------------------------------------------------------------ the spec

def test_defaults_are_sane():
    spec = SemanticSpec.parse({"columns": ["d"]})
    assert spec.threshold == 0.85
    assert spec.min_length == 4
    assert spec.model == DEFAULT_MODEL
    assert spec.reference is None


@pytest.mark.parametrize("threshold", [0, -0.1, 1.5, "high"])
def test_a_threshold_outside_zero_to_one_is_refused(threshold):
    # 0 matches everything and >1 matches nothing; either is a rule that looks like
    # it ran and reported the wrong answer.
    with pytest.raises(SemanticRuleError):
        SemanticSpec.parse({"columns": ["d"], "threshold": threshold})


def test_threshold_of_exactly_one_is_allowed():
    assert SemanticSpec.parse({"columns": ["d"], "threshold": 1}).threshold == 1.0


def test_columns_are_required():
    with pytest.raises(SemanticRuleError, match="names no column"):
        SemanticSpec.parse({})


def test_reference_must_name_the_corpus_in_full():
    with pytest.raises(SemanticRuleError, match="missing table, column"):
        SemanticSpec.parse({"columns": ["d"], "reference": {"schema": "S"}})


# ------------------------------------------------------- identifier handling

def test_identifiers_are_quoted_and_upper_cased():
    assert quote_ident("prd_model_final", "x") == '"PRD_MODEL_FINAL"'


@pytest.mark.parametrize("bad", [
    'PRD"; DROP TABLE X --', "PRD MODEL", "1TABLE", "", None, "a-b",
])
def test_an_identifier_that_is_not_an_identifier_is_refused(bad):
    # Reference names arrive in a request body and are used to build SQL.
    with pytest.raises(SemanticRuleError, match="not a plain identifier"):
        quote_ident(bad, "reference.table")


def test_a_quote_in_the_model_name_is_refused():
    # The model is interpolated into the statement, so it cannot carry a quote.
    with pytest.raises(SemanticRuleError, match="contains a quote"):
        SemanticSpec.parse({"columns": ["d"], "model": "X' OR '1'='1"})


# ------------------------------------------------------------- candidate set

def test_blanks_and_nulls_are_not_candidates():
    # A blank is not a near-duplicate of another blank - that is `required`'s job,
    # and reporting it here would flag every incomplete row twice.
    assert candidate_values([None, "", "   ", "hello"], 4) == ["hello"]


def test_values_shorter_than_min_length_are_dropped():
    assert candidate_values(["VERP", "AB", "1223"], 4) == ["VERP", "1223"]


def test_candidates_are_distinct_and_order_is_stable():
    assert candidate_values(["beta", "alpha", "beta"], 4) == ["beta", "alpha"]


def test_whitespace_is_trimmed_before_comparing():
    assert candidate_values(["  hello  ", "hello"], 4) == ["hello"]


# ------------------------------------------------------------- the SQL built

def test_within_file_query_excludes_the_self_pair():
    # Without `a.VAL < b.VAL` every value is its own duplicate at similarity 1.0.
    sql = within_file_sql(SemanticSpec.parse({"columns": ["d"]}), 2)
    assert "a.VAL < b.VAL" in sql
    assert sql.count("SELECT ? AS VAL FROM DUMMY") == 2


def test_within_file_query_carries_the_model_and_a_threshold_placeholder():
    sql = within_file_sql(SemanticSpec.parse({"columns": ["d"]}), 3)
    assert DEFAULT_MODEL in sql
    assert sql.count("?") == 4          # three values plus the threshold


def test_reference_query_uses_a_stored_vector_when_one_is_named():
    spec = SemanticSpec.parse({"columns": ["d"], "reference": {
        "schema": "simplemdg_prd", "table": "prd_model_staging_productdescription",
        "column": "productdescription", "key_column": "objectid",
        "embedding_column": "desc_vector"}})
    sql = reference_sql(spec, 1)
    assert 'r."DESC_VECTOR"' in sql
    # The whole point of a stored vector is not re-embedding the corpus.
    assert "VECTOR_EMBEDDING(r." not in sql
    assert '"SIMPLEMDG_PRD"."PRD_MODEL_STAGING_PRODUCTDESCRIPTION"' in sql


def test_reference_query_embeds_the_corpus_when_no_vector_is_stored():
    spec = SemanticSpec.parse({"columns": ["d"], "reference": {
        "schema": "S", "table": "T", "column": "C"}})
    assert "VECTOR_EMBEDDING(r.\"C\"" in reference_sql(spec, 1)


def test_a_reference_filter_is_applied():
    # SMDG's own search restricts to open change requests exactly this way.
    spec = SemanticSpec.parse({"columns": ["d"], "reference": {
        "schema": "S", "table": "T", "column": "C",
        "filter": "MDGSTATUS IN ('REVIEW','PROCESSING')"}})
    assert "(MDGSTATUS IN ('REVIEW','PROCESSING')) AND" in reference_sql(spec, 1)


# ------------------------------------------------------------ reducing rows

def test_both_sides_of_a_within_file_pair_are_reported():
    """Flagging only the second of a pair would make the verdict depend on row
    order, which is not a property of the data."""
    db = FakeDB([(TIE_A, TIE_B, 0.8960)])
    found = SemanticMatcher(SemanticSpec.parse({"columns": ["d"]}), db).find([TIE_A, TIE_B])
    assert set(found) == {TIE_A, TIE_B}
    assert found[TIE_A].matched == TIE_B
    assert found[TIE_B].matched == TIE_A
    assert found[TIE_A].score == pytest.approx(0.8960)


def test_the_best_match_wins_when_a_value_matches_several():
    # A reference row is (value, match_key, match_value, score).
    db = FakeDB([("a-value", "K1", "weak", 0.86), ("a-value", "K2", "strong", 0.97)])
    spec = SemanticSpec.parse({"columns": ["d"], "reference": {
        "schema": "S", "table": "T", "column": "C"}})
    found = SemanticMatcher(spec, db).find(["a-value"])
    assert found["a-value"].matched == "strong"
    assert found["a-value"].score == pytest.approx(0.97)


def test_one_value_alone_cannot_duplicate_itself():
    db = FakeDB()
    assert SemanticMatcher(SemanticSpec.parse({"columns": ["d"]}), db).find(["only"]) == {}
    assert db.calls == []                # and no query is sent


def test_one_value_is_still_worth_asking_against_a_reference():
    db = FakeDB([("only-one", "K1", "close enough", 0.91)])
    spec = SemanticSpec.parse({"columns": ["d"], "reference": {
        "schema": "S", "table": "T", "column": "C", "key_column": "K"}})
    found = SemanticMatcher(spec, db).find(["only-one"])
    assert found["only-one"].key == "K1"


def test_the_threshold_is_passed_as_a_parameter_not_inlined():
    db = FakeDB()
    spec = SemanticSpec.parse({"columns": ["d"], "threshold": 0.93})
    SemanticMatcher(spec, db).find(["alpha", "beta"])
    sql, params = db.calls[0]
    assert params == ["alpha", "beta", 0.93]
    assert "0.93" not in sql


def test_a_missing_embedding_column_is_warned_about_not_tolerated_silently():
    # ~161 s per query against an 11k corpus. Silence here reads as "it is fine".
    db = FakeDB()
    spec = SemanticSpec.parse({"columns": ["d"], "reference": {
        "schema": "S", "table": "T", "column": "C"}})
    matcher = SemanticMatcher(spec, db)
    matcher.find(["something long enough"])
    assert any("embedding_column" in w for w in matcher.warnings)


def test_large_inputs_are_batched():
    db = FakeDB()
    spec = SemanticSpec.parse({"columns": ["d"]})
    SemanticMatcher(spec, db).find([f"value-number-{i}" for i in range(MAX_VALUES_PER_QUERY + 10)])
    assert len(db.calls) == 2
    assert len(db.calls[0][1]) == MAX_VALUES_PER_QUERY + 1     # values + threshold


# -------------------------------------------------------------- at the edge

def test_boundary_accepts_a_full_rule():
    validate_validation_rules([{
        "type": "semantic_unique",
        "params": {"columns": ["ProductDescription.Product Description"],
                   "threshold": 0.85, "min_length": 4,
                   "reference": {"schema": "SIMPLEMDG_PRD",
                                 "table": "PRD_MODEL_STAGING_PRODUCTDESCRIPTION",
                                 "column": "PRODUCTDESCRIPTION",
                                 "key_column": "OBJECTID",
                                 "embedding_column": "DESC_VECTOR",
                                 "filter": "MDGSTATUS IN ('REVIEW','PROCESSING')"}},
    }])


def test_boundary_rejects_a_bad_threshold():
    with pytest.raises(RuleSpecError, match="threshold"):
        validate_validation_rules([{
            "type": "semantic_unique",
            "params": {"columns": ["d"], "threshold": 2.0}}])


def test_boundary_rejects_an_incomplete_reference():
    with pytest.raises(RuleSpecError, match="reference"):
        validate_validation_rules([{
            "type": "semantic_unique",
            "params": {"columns": ["d"], "reference": {"schema": "S"}}}])


def test_boundary_rejects_an_unknown_param():
    with pytest.raises(RuleSpecError, match="unknown param"):
        validate_validation_rules([{
            "type": "semantic_unique",
            "params": {"columns": ["d"], "treshold": 0.9}}])


# ------------------------------------------- through the real validator pass

class _Rule:
    def __init__(self, **params):
        self.rule_name = "DESC_SEMANTIC_DUP"
        self.type = "semantic_unique"
        self.error_message = "description resembles an existing product"
        self.params = params
        self.field_paths = {"desc": "ProductDescription.productDescription"}


def _provider(executor):
    from app.layer4_frameworks.providers.validation.polars_validator_provider import (
        PolarsValidatorProvider,
    )
    import logging
    provider = PolarsValidatorProvider(logging.getLogger("t"), storage=None)
    provider.semantic_executor = executor
    return provider


def test_the_pass_flags_both_rows_and_names_what_they_resemble():
    import polars as pl
    db = FakeDB([(TIE_A, TIE_B, 0.8960)])
    frame = pl.DataFrame({"desc": ["something else entirely", TIE_A, TIE_B]})
    exprs, mapping = _provider(db)._process_semantic_rules(
        [_Rule(columns=["desc"])], frame)

    out = frame.with_columns(exprs)
    hits = [c for c in out.columns if c.startswith("sem_")]
    flagged = [r for r in out.select(hits).iter_rows() if any(x is not None for x in r)]
    assert len(flagged) == 2
    # The report says WHAT it resembles, not only that it resembles something.
    assert "0.8960" in flagged[0][0]["value"]
    assert flagged[0][0]["path"] == "ProductDescription.productDescription"
    assert len(mapping) == 1


def test_a_clean_column_produces_no_expression():
    import polars as pl
    frame = pl.DataFrame({"desc": ["alpha thing", "beta thing"]})
    exprs, mapping = _provider(FakeDB([]))._process_semantic_rules(
        [_Rule(columns=["desc"])], frame)
    assert exprs == [] and mapping == {}


def test_a_column_the_file_does_not_carry_is_an_error_not_a_skip():
    import polars as pl
    frame = pl.DataFrame({"desc": ["alpha thing"]})
    with pytest.raises(SemanticRuleError, match="not in the file"):
        _provider(FakeDB([]))._process_semantic_rules([_Rule(columns=["nope"])], frame)


def test_without_a_connection_or_an_executor_the_rule_refuses_to_run():
    """A duplication check that quietly returns nothing looks exactly like a clean
    file. That is the one outcome worse than an error."""
    import polars as pl, logging
    from app.layer4_frameworks.providers.validation.polars_validator_provider import (
        PolarsValidatorProvider,
    )
    provider = PolarsValidatorProvider(logging.getLogger("t"), storage=None)
    frame = pl.DataFrame({"desc": ["alpha thing", "beta thing"]})
    with pytest.raises(SemanticRuleError, match="reference.connection"):
        provider._process_semantic_rules([_Rule(columns=["desc"])], frame)


def test_the_connection_names_host_and_user_and_the_payload_owns_them():
    from app.layer4_frameworks.providers.validation.semantic_match import Connection
    c = Connection.parse({"host": "h.example", "user": "RT_USER", "port": "443"})
    assert (c.host, c.user, c.port) == ("h.example", "RT_USER", 443)


def test_a_connection_without_a_host_is_refused():
    from app.layer4_frameworks.providers.validation.semantic_match import Connection
    with pytest.raises(SemanticRuleError, match="missing host"):
        Connection.parse({"user": "U"})
