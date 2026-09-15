"""Tests for the SQL security layer — the worker's trust boundary."""

from app.layer1_domain.value_objects.operation_type import OperationType
from app.layer1_domain.value_objects.database_type import DatabaseType
from app.layer2_application.services.sql_validator import SQLValidator


class TestDetectOperationType:
    def test_detects_each_operation(self):
        v = SQLValidator()
        assert v.detect_operation_type("SELECT * FROM t") == OperationType.SELECT
        assert v.detect_operation_type("insert into t values (1)") == OperationType.INSERT
        assert v.detect_operation_type("  UPDATE t SET a=1") == OperationType.UPDATE
        assert v.detect_operation_type("DELETE FROM t") == OperationType.DELETE
        assert v.detect_operation_type("DROP TABLE t") == OperationType.DDL

    def test_unknown_returns_none(self):
        assert SQLValidator().detect_operation_type("WITH cte AS (...)") is None


class TestValidateSelectOnly:
    def test_valid_select(self):
        res = SQLValidator().validate_select_only("SELECT id FROM users")
        assert res.is_valid
        assert res.detected_operation == OperationType.SELECT
        assert res.sanitized_sql == "SELECT id FROM users"

    def test_empty_rejected(self):
        assert SQLValidator().validate_select_only("   ").is_valid is False

    def test_non_select_rejected(self):
        res = SQLValidator().validate_select_only("DELETE FROM users")
        assert res.is_valid is False
        assert res.detected_operation == OperationType.DELETE

    def test_destructive_keyword_blocked(self):
        # A SELECT that smuggles a destructive keyword must be rejected.
        res = SQLValidator().validate_select_only("SELECT * FROM t WHERE x IN (DELETE FROM y)")
        assert res.is_valid is False
        assert "DELETE" in res.error

    def test_multi_statement_blocked_by_default(self):
        res = SQLValidator().validate_select_only("SELECT 1; DROP TABLE t")
        assert res.is_valid is False
        assert "Multiple" in res.error

    def test_multi_statement_allowed_when_enabled(self):
        # Still blocked: second statement is destructive, caught as a keyword.
        res = SQLValidator(allow_multi_statement=True).validate_select_only("SELECT 1; SELECT 2")
        assert res.is_valid is True


class TestValidateOperation:
    def test_insert_matches(self):
        res = SQLValidator().validate_operation("INSERT INTO t VALUES (1)", OperationType.INSERT)
        assert res.is_valid

    def test_mismatch_rejected(self):
        res = SQLValidator().validate_operation("INSERT INTO t VALUES (1)", OperationType.UPDATE)
        assert res.is_valid is False

    def test_select_routes_to_strict_validation(self):
        res = SQLValidator().validate_operation("DELETE FROM t", OperationType.SELECT)
        assert res.is_valid is False


class TestInjectLimit:
    def test_adds_limit_when_absent(self):
        assert SQLValidator(max_rows=50).inject_limit("SELECT * FROM t") == "SELECT * FROM t LIMIT 50"

    def test_preserves_existing_limit(self):
        assert SQLValidator().inject_limit("SELECT * FROM t LIMIT 5") == "SELECT * FROM t LIMIT 5"

    def test_strips_trailing_semicolon(self):
        assert SQLValidator(max_rows=10).inject_limit("SELECT 1;") == "SELECT 1 LIMIT 10"


class TestSanitizeForLogging:
    def test_truncates_long_sql(self):
        out = SQLValidator().sanitize_for_logging("x" * 600, max_length=500)
        assert out.endswith("... [TRUNCATED]")
        assert len(out) == 500 + len("... [TRUNCATED]")

    def test_short_sql_untouched(self):
        assert SQLValidator().sanitize_for_logging("SELECT 1") == "SELECT 1"


class TestEnums:
    def test_operation_from_string_roundtrip(self):
        assert OperationType.from_string("SELECT") == OperationType.SELECT
        assert OperationType.from_string("  delete ") == OperationType.DELETE

    def test_operation_from_string_invalid(self):
        import pytest
        with pytest.raises(ValueError):
            OperationType.from_string("nope")

    def test_operation_properties(self):
        assert OperationType.SELECT.is_read_only
        assert OperationType.DELETE.is_destructive
        assert not OperationType.SELECT.is_destructive

    def test_database_type_from_string(self):
        assert DatabaseType.from_string("POSTGRESQL") == DatabaseType.POSTGRESQL
        import pytest
        with pytest.raises(ValueError):
            DatabaseType.from_string("oracle")
