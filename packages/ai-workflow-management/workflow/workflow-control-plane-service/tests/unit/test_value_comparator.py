"""Exhaustive tests for the shared CONDITION/SWITCH value comparator.

Each operator gets its own class. Cases cover the type-flexibility we promise:
operator-name case, string<->number coercion, bool handling, and falsy-empty
semantics. When adding a new operator to value_comparator.compare_values,
add a class here.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.value_objects.comparison import Comparison


class TestOperatorCaseInsensitive:
    """`op` is normalized via `.lower()`; aliases and casing should all work."""

    @pytest.mark.parametrize("op", ["==", "=", "Equals", "EQUAL", "Equal"])
    def test_equality_aliases(self, op):
        assert Comparison.evaluate(1, op, 1) is True

    @pytest.mark.parametrize("op", ["!=", "Not_Equals", "NOT_EQUAL"])
    def test_inequality_aliases(self, op):
        assert Comparison.evaluate(1, op, 2) is True

    def test_unknown_operator_returns_false(self):
        assert Comparison.evaluate(1, "wat", 1) is False
        assert Comparison.evaluate(1, "", 1) is False
        assert Comparison.evaluate(1, None, 1) is False  # type: ignore[arg-type]


class TestEquality:
    @pytest.mark.parametrize("op", ["=", "==", "equals", "equal"])
    def test_int_equal(self, op):
        assert Comparison.evaluate(3, op, 3) is True
        assert Comparison.evaluate(3, op, 4) is False

    def test_string_numeric_equal(self):
        # The cross-type case (input may arrive as int or str)
        assert Comparison.evaluate("3", "==", 3) is True
        assert Comparison.evaluate(3, "==", "3") is True
        assert Comparison.evaluate("3.0", "==", 3) is True
        assert Comparison.evaluate(3, "==", 3.0) is True

    def test_lowercased_string_equal(self):
        assert Comparison.evaluate("YES", "==", "yes") is True
        assert Comparison.evaluate("Active", "==", "active") is True
        assert Comparison.evaluate("Active", "==", "Inactive") is False

    def test_none_equal(self):
        # Both sides become "" when None — equal.
        assert Comparison.evaluate(None, "==", None) is True
        # None compared to "" also lower-cases to "" — equal (matches frontend behavior).
        assert Comparison.evaluate(None, "==", "") is True


class TestInequality:
    @pytest.mark.parametrize("op", ["!=", "not_equals", "not_equal"])
    def test_int_not_equal(self, op):
        assert Comparison.evaluate(3, op, 4) is True
        assert Comparison.evaluate(3, op, 3) is False


class TestOrderedComparisons:
    """Numeric coercion is preferred so `"10" > "9"` is True, not lex False."""

    @pytest.mark.parametrize("op", [">", "gt", "greater_than"])
    def test_gt(self, op):
        assert Comparison.evaluate(10, op, 9) is True
        assert Comparison.evaluate("10", op, "9") is True   # critical: string-vs-string numeric
        assert Comparison.evaluate("3", op, 2) is True
        assert Comparison.evaluate(2, op, 3) is False

    @pytest.mark.parametrize("op", [">=", "gte", "greater_or_equal"])
    def test_gte(self, op):
        assert Comparison.evaluate(3, op, 3) is True
        assert Comparison.evaluate("3.0", op, 3) is True
        assert Comparison.evaluate(2, op, 3) is False

    @pytest.mark.parametrize("op", ["<", "lt", "less_than"])
    def test_lt(self, op):
        assert Comparison.evaluate(2, op, 3) is True
        assert Comparison.evaluate("9", op, "10") is True
        assert Comparison.evaluate(3, op, 3) is False

    @pytest.mark.parametrize("op", ["<=", "lte", "less_or_equal"])
    def test_lte(self, op):
        assert Comparison.evaluate(3, op, 3) is True
        assert Comparison.evaluate(4, op, 3) is False

    def test_non_numeric_falls_back_to_string(self):
        # "banana" > "apple" lexicographically — the fallback path.
        assert Comparison.evaluate("banana", ">", "apple") is True
        assert Comparison.evaluate("apple", "<", "banana") is True


class TestBool:
    """`bool` is a subclass of `int`, but `float(str(True))` raises — so the
    comparator handles bool explicitly to map True→1.0, False→0.0."""

    def test_bool_equals_number(self):
        assert Comparison.evaluate(True, "==", 1) is True
        assert Comparison.evaluate(False, "==", 0) is True
        assert Comparison.evaluate(True, "==", 0) is False

    def test_bool_ordered(self):
        assert Comparison.evaluate(True, ">", False) is True
        assert Comparison.evaluate(False, "<", True) is True

    def test_bool_string_compare(self):
        # When numeric coercion fails on the right side, falls back to string:
        # "true" vs "yes" — both lowercased — not equal.
        assert Comparison.evaluate(True, "==", "yes") is False
        # But True can compare to int via numeric path.
        assert Comparison.evaluate(True, "!=", 0) is True


class TestContains:
    def test_contains_substring(self):
        assert Comparison.evaluate("hello world", "contains", "world") is True
        assert Comparison.evaluate("hello", "contains", "xyz") is False

    def test_not_contains(self):
        assert Comparison.evaluate("hello", "not_contains", "xyz") is True
        assert Comparison.evaluate("hello", "not_contains", "ell") is False

    def test_contains_stringifies(self):
        # int gets stringified: 12345 contains "234"
        assert Comparison.evaluate(12345, "contains", "234") is True


class TestStartsEndsWith:
    def test_starts_with(self):
        assert Comparison.evaluate("user-42", "starts_with", "user") is True
        assert Comparison.evaluate("admin-1", "starts_with", "user") is False

    def test_ends_with(self):
        assert Comparison.evaluate("file.csv", "ends_with", ".csv") is True
        assert Comparison.evaluate("file.txt", "ends_with", ".csv") is False


class TestEmpty:
    @pytest.mark.parametrize("falsy", [None, "", [], {}])
    def test_is_empty_truthy(self, falsy):
        assert Comparison.evaluate(falsy, "is_empty", None) is True

    @pytest.mark.parametrize("non_empty", ["x", [1], {"a": 1}, 0, False])
    def test_is_empty_does_not_treat_zero_or_false_as_empty(self, non_empty):
        # 0 and False are not "empty" — only None/""/[]/{} are.
        assert Comparison.evaluate(non_empty, "is_empty", None) is False

    @pytest.mark.parametrize("non_empty", ["x", [1], {"a": 1}, 0, False])
    def test_is_not_empty_truthy(self, non_empty):
        assert Comparison.evaluate(non_empty, "is_not_empty", None) is True

    @pytest.mark.parametrize("falsy", [None, "", [], {}])
    def test_is_not_empty_falsy(self, falsy):
        assert Comparison.evaluate(falsy, "is_not_empty", None) is False


class TestIn:
    def test_in_list(self):
        assert Comparison.evaluate("a", "in", ["a", "b", "c"]) is True
        assert Comparison.evaluate("z", "in", ["a", "b", "c"]) is False

    def test_in_tuple_and_set(self):
        assert Comparison.evaluate(2, "in", (1, 2, 3)) is True
        assert Comparison.evaluate(2, "in", {1, 2, 3}) is True

    def test_in_with_stringified_values(self):
        # Frontend often delivers list values as strings; numeric input still matches.
        assert Comparison.evaluate(2, "in", ["1", "2", "3"]) is True
        assert Comparison.evaluate("2", "in", [1, 2, 3]) is True

    def test_in_string_substring(self):
        assert Comparison.evaluate("ab", "in", "abcdef") is True
        assert Comparison.evaluate("xyz", "in", "abcdef") is False

    def test_in_unsupported_right(self):
        assert Comparison.evaluate("a", "in", 42) is False  # type: ignore[arg-type]


class TestNotIn:
    def test_not_in_list(self):
        assert Comparison.evaluate("z", "not_in", ["a", "b", "c"]) is True
        assert Comparison.evaluate("a", "not_in", ["a", "b", "c"]) is False

    def test_not_in_with_stringified_values(self):
        assert Comparison.evaluate(4, "not_in", ["1", "2", "3"]) is True
        assert Comparison.evaluate(2, "not_in", ["1", "2", "3"]) is False

    def test_not_in_string(self):
        assert Comparison.evaluate("xyz", "not_in", "abcdef") is True
        assert Comparison.evaluate("ab", "not_in", "abcdef") is False


class TestRegex:
    def test_match(self):
        assert Comparison.evaluate("user-42", "regex", r"^user-\d+$") is True

    def test_no_match(self):
        assert Comparison.evaluate("nope", "regex", r"^user-\d+$") is False

    def test_search_semantics(self):
        # `search`, not `fullmatch` — partial matches are accepted.
        assert Comparison.evaluate("prefix-user-42-suffix", "regex", r"user-\d+") is True

    def test_invalid_pattern_returns_false(self):
        # Malformed pattern doesn't crash the workflow.
        assert Comparison.evaluate("x", "regex", "[unclosed") is False

    def test_stringifies_left(self):
        assert Comparison.evaluate(12345, "regex", r"^\d+$") is True
