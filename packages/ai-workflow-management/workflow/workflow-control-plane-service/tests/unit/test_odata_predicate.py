"""SA-2263 — the CONDITION → ``$filter`` translator and its guards.

One test per operator, as the ticket asks, plus the two guards that decide
whether this is a memory win or a memory move:

* an untranslatable predicate must return ``None`` so the caller falls back to
  the row-range path — never to a full materialize, and never to a guessed
  filter, which would silently change which rows match;
* an Excel-backed ref must be refused, because ``pl.read_excel`` is EAGER and
  filtering it merely relocates the OOM into the file service.
"""

from __future__ import annotations

import pytest

from app.layer2_application.resolve import odata_predicate as op
from app.layer2_application.resolve.odata_predicate import (
    COMPARISON_OPERATORS,
    UnsupportedPredicate,
    count_query,
    is_lazily_queryable,
    require_filter,
    translate,
    translate_all,
)


class TestOneTestPerOperator:
    def test_equals(self):
        assert translate("sku", "==", "A1") == "sku eq 'A1'"

    def test_not_equals(self):
        assert translate("sku", "!=", "A1") == "sku ne 'A1'"

    def test_greater_than(self):
        assert translate("qty", ">", 5) == "qty gt 5"

    def test_greater_or_equal(self):
        assert translate("qty", ">=", 5) == "qty ge 5"

    def test_less_than(self):
        assert translate("qty", "<", 5) == "qty lt 5"

    def test_less_or_equal(self):
        assert translate("qty", "<=", 5) == "qty le 5"

    def test_in(self):
        # NO outer parentheses: the file service's parser splits on ' or ' with
        # a depth-aware splitter, so a wrapping pair keeps the whole expression
        # at depth 1, nothing splits, and it is parsed as ONE predicate — which
        # fails. Corrected when the real grammar was read; nothing consumed the
        # old form, so nothing regressed.
        assert translate("sku", "IN", ["A1", "B2"]) == "sku eq 'A1' or sku eq 'B2'"

    def test_contains(self):
        assert translate("name", "CONTAINS", "Acme") == "contains(name, 'Acme')"

    def test_every_operator_the_evaluator_knows_is_covered(self):
        """If an operator is added to ConditionEvaluator and not here, this
        fails — instead of that operator silently degrading to a full read."""
        from app.layer2_application.resolve.variable_resolver import (
            _CONDITION_OP_PATTERN,
        )
        import re

        # The alternation inside the second capture group of the evaluator's
        # pattern IS its operator table.
        alternation = re.search(r"\(([^)]*\|[^)]*)\)", _CONDITION_OP_PATTERN.pattern)
        assert alternation, "the evaluator's operator table moved"
        declared = {tok for tok in alternation.group(1).split("|")}
        assert declared == COMPARISON_OPERATORS, (
            f"operator tables have drifted: evaluator={declared} "
            f"translator={COMPARISON_OPERATORS}"
        )


class TestLiteralsAndEscaping:
    def test_single_quotes_are_doubled(self):
        assert translate("name", "==", "O'Brien") == "name eq 'O''Brien'"

    @pytest.mark.parametrize("value,expected", [
        (True, "flag eq true"),
        (False, "flag eq false"),
        (0, "flag eq 0"),
        (3.5, "flag eq 3.5"),
        (None, "flag eq null"),
        ("", "flag eq ''"),
    ])
    def test_literal_kinds(self, value, expected):
        assert translate("flag", "==", value) == expected

    def test_an_unrenderable_value_is_untranslatable(self):
        assert translate("f", "==", {"a": 1}) is None
        assert translate("f", "==", object()) is None

    def test_alias_equals_sign(self):
        assert translate("sku", "=", "A1") == "sku eq 'A1'"

    def test_operator_case_is_ignored(self):
        assert translate("sku", "in", ["A"]) == "sku eq 'A'"
        assert translate("n", "contains", "x") == "contains(n, 'x')"


class TestUntranslatableFallsBackRatherThanGuessing:
    @pytest.mark.parametrize("bad_op", ["~=", "LIKE", "MATCHES", "", "BETWEEN"])
    def test_unknown_operators_return_none(self, bad_op):
        assert translate("sku", bad_op, "x") is None

    @pytest.mark.parametrize("bad_field", [
        "sku; DROP", "sku eq 'x'", "a b", "(sku)", "sku'", "", "   ", "x" * 200,
    ])
    def test_unsafe_field_names_return_none(self, bad_field):
        assert translate(bad_field, "==", "x") is None, (
            f"{bad_field!r} was interpolated into a query string"
        )

    def test_empty_in_list_returns_none(self):
        assert translate("sku", "IN", []) is None
        assert translate("sku", "IN", 5) is None

    def test_require_filter_raises_for_tests_only(self):
        with pytest.raises(UnsupportedPredicate):
            require_filter("sku", "LIKE", "x")


class TestAllOrNothing:
    def test_all_rules_translate(self):
        rules = [
            {"key": "qty", "operations": ">", "value": 5},
            {"key": "sku", "operations": "==", "value": "A1"},
        ]
        filt, untranslated = translate_all(rules)
        assert filt == "qty gt 5 and sku eq 'A1'"
        assert untranslated == []

    def test_one_untranslatable_rule_discards_the_whole_filter(self):
        """A partial AND filter returns MORE rows than the predicate allows.

        Acting on those rows would mean acting on data the condition excluded —
        strictly worse than falling back and reading normally.
        """
        rules = [
            {"key": "qty", "operations": ">", "value": 5},
            {"key": "sku", "operations": "LIKE", "value": "A%"},
        ]
        filt, untranslated = translate_all(rules)
        assert filt is None, "a partial filter was returned for an ANDed rule set"
        assert len(untranslated) == 1
        assert untranslated[0]["reason"] == "untranslatable"

    def test_no_rules_is_not_a_filter(self):
        assert translate_all([]) == (None, [])

    def test_a_non_mapping_rule_is_reported(self):
        filt, untranslated = translate_all(["nonsense"])
        assert filt is None
        assert untranslated[0]["reason"] == "not-a-mapping"


class TestExcelGuard:
    """pl.read_excel is EAGER — filtering an xlsx moves the OOM, not removes it."""

    @pytest.mark.parametrize("ref", [
        {"content_type": "text/csv"},
        {"content_type": "text/csv; charset=utf-8"},
        {"file_name": "orders.csv"},
    ])
    def test_csv_is_lazily_queryable(self, ref):
        assert is_lazily_queryable(ref) is True

    @pytest.mark.parametrize("ref", [
        {"content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        {"file_name": "orders.xlsx"},
        {"file_name": "orders.xls"},
        {"content_type": "application/json"},
    ])
    def test_non_csv_is_refused(self, ref):
        assert is_lazily_queryable(ref) is False, (
            "an eagerly-read format was accepted for pushdown — the OOM just "
            "moves into the file service"
        )

    def test_an_undeclared_format_is_refused(self):
        """Unknown must not read as safe."""
        assert is_lazily_queryable({}) is False
        assert is_lazily_queryable({"file_id": "f-1"}) is False
        assert is_lazily_queryable(None) is False


class TestCountQueryTransfersNoRows:
    def test_count_query_asks_for_zero_rows(self):
        q = count_query("qty gt 5")
        assert q["$count"] == "true"
        assert q["$top"] == "0", (
            "$count without $top=0 lets the service return the matching rows "
            "alongside the number — the transfer this exists to avoid"
        )
        assert q["$filter"] == "qty gt 5"


class TestAgainstTheRealFileService:
    """The mock implements the same OData-lite contract the CP talks to."""

    async def test_filter_and_count_agree_with_python(self):
        from app.layer2_application.data.csv_converter import CsvConverter
        from app.layer2_application.data.data_store_service import DataStoreService
        from app.layer4_frameworks.clients.mock_file_service_client import (
            MockFileServiceClient,
        )

        client = MockFileServiceClient()
        store = DataStoreService(client, CsvConverter())
        rows = [{"sku": f"S{i}", "qty": i} for i in range(50)]
        ref = await store.store_data("n", rows)

        filt = translate("sku", "==", "S7")
        result = await client.query_file_data(ref["file_id"], {"$filter": filt})
        returned = result.get("data", [])
        expected = [r for r in rows if r["sku"] == "S7"]
        assert len(returned) == len(expected) == 1
        assert returned[0]["sku"] == "S7"
