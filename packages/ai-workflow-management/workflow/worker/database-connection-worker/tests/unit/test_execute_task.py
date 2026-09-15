"""Unit tests for the Database Connection Worker execute-task use case.

The DB is mocked at the ``DatabaseEngineFactory.create_engine`` seam (patched in the
use-case module namespace) so no live database is ever contacted — the engine, its
``connect()`` context manager, and the executed result are all ``MagicMock`` (every
call here is sync — MagicMock, never AsyncMock). Expectations are grounded in the
source of ``execute_task_usecase.py``.
"""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    ExecuteTaskCommand,
    DATABASE_CONNECTION_INPUT_SCHEMA,
    DATABASE_CONNECTION_OUTPUT_SCHEMA,
    DEFAULT_MAX_ROWS,
    DEFAULT_TIMEOUT,
)

_USECASE_MOD = "app.layer2_application.features.execute_task.use_cases.execute_task_usecase"

# A resolvable vault basic credential — required after the T5 hard-remove.
_CRED = {
    "credential_ref": "c1",
    "__credentials": {"c1": {"type": "basic",
                             "config": {"username": "u", "password": "p"}}},
}


def _mock_engine(*, columns=None, rows=None, rowcount=0):
    """Build a MagicMock engine whose connect() yields a connection returning the
    given SELECT columns/rows (for _execute_select) or rowcount (for _execute_write).
    """
    result = MagicMock()
    result.keys.return_value = columns or []
    result.fetchall.return_value = rows or []
    result.rowcount = rowcount

    conn = MagicMock()
    conn.execute.return_value = result
    # context manager: `with engine.connect() as conn`
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    # transaction context manager: `with conn.begin()`
    txn = MagicMock()
    txn.__enter__ = MagicMock(return_value=txn)
    txn.__exit__ = MagicMock(return_value=False)
    conn.begin.return_value = txn

    engine = MagicMock()
    engine.connect.return_value = conn
    return engine, conn, result


def _cmd(inputs, task_id="t1"):
    return ExecuteTaskCommand(task_id=task_id, action="database_connection", inputs=inputs)


class TestSelectHappyPath:
    @patch(f"{_USECASE_MOD}.DatabaseEngineFactory.create_engine")
    def test_select_returns_rows_as_dicts(self, mock_create, logger, monitor):
        engine, conn, _ = _mock_engine(
            columns=["id", "name"],
            rows=[(1, "alice"), (2, "bob")],
        )
        mock_create.return_value = engine

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd({
            "db_type": "postgresql", "host": "db", "database": "app",
            "operation_type": "select", "raw_sql": "SELECT id, name FROM users",
            **_CRED,
        }))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["success"] is True
        assert result.outputs["data"] == [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]
        assert result.outputs["row_count"] == 2
        assert result.outputs["affected_rows"] == 0
        # execute() annotates operation_type + execution_time_ms onto the result.
        assert result.outputs["operation_type"] == "select"
        assert "execution_time_ms" in result.outputs
        engine.dispose.assert_called_once()

    @patch(f"{_USECASE_MOD}.DatabaseEngineFactory.create_engine")
    def test_select_injects_limit_when_absent(self, mock_create, logger, monitor):
        engine, conn, _ = _mock_engine(columns=["id"], rows=[])
        mock_create.return_value = engine

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        uc.execute(_cmd({
            "db_type": "postgresql", "host": "db", "database": "app",
            "operation_type": "select", "raw_sql": "SELECT id FROM t",
            "max_rows": 25, **_CRED,
        }))

        # The SQL passed to text() should carry the injected LIMIT 25.
        executed_sql = str(conn.execute.call_args.args[0])
        assert "LIMIT 25" in executed_sql

    @patch(f"{_USECASE_MOD}.DatabaseEngineFactory.create_engine")
    def test_select_empty_result_row_count_zero(self, mock_create, logger, monitor):
        engine, _, _ = _mock_engine(columns=["id"], rows=[])
        mock_create.return_value = engine

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd({
            "db_type": "mysql", "host": "db", "database": "app",
            "operation_type": "select", "raw_sql": "SELECT id FROM t",
            **_CRED,
        }))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["data"] == []
        assert result.outputs["row_count"] == 0


class TestWriteHappyPath:
    @patch(f"{_USECASE_MOD}.DatabaseEngineFactory.create_engine")
    def test_insert_reports_affected_rows(self, mock_create, logger, monitor):
        engine, conn, _ = _mock_engine(rowcount=3)
        mock_create.return_value = engine

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd({
            "db_type": "postgresql", "host": "db", "database": "app",
            "operation_type": "insert",
            "raw_sql": "INSERT INTO t (a) VALUES (1), (2), (3)",
            **_CRED,
        }))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["affected_rows"] == 3
        assert result.outputs["data"] == []
        assert result.outputs["operation_type"] == "insert"
        # Write path opens a transaction.
        conn.begin.assert_called_once()
        engine.dispose.assert_called_once()

    @patch(f"{_USECASE_MOD}.DatabaseEngineFactory.create_engine")
    def test_update_uses_transaction(self, mock_create, logger, monitor):
        engine, conn, _ = _mock_engine(rowcount=1)
        mock_create.return_value = engine

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd({
            "db_type": "postgresql", "host": "db", "database": "app",
            "operation_type": "update", "raw_sql": "UPDATE t SET a=1 WHERE id=1",
            **_CRED,
        }))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["affected_rows"] == 1
        conn.begin.assert_called_once()


class TestValidationErrors:
    def test_missing_raw_sql_is_validation_error(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd({
            "db_type": "postgresql", "host": "db", "database": "app",
            "operation_type": "select", "raw_sql": "   ", **_CRED,
        }))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"
        assert "raw_sql" in result.error
        assert result.outputs["success"] is False

    def test_missing_db_type_is_validation_error(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd({
            "host": "db", "database": "app",
            "operation_type": "select", "raw_sql": "SELECT 1", **_CRED,
        }))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"
        assert "db_type" in result.error

    def test_missing_operation_type_is_validation_error(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd({
            "db_type": "postgresql", "host": "db", "database": "app",
            "raw_sql": "SELECT 1", **_CRED,
        }))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"
        assert "operation_type" in result.error

    def test_invalid_db_type_string_is_validation_error(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd({
            "db_type": "oracle", "host": "db", "database": "app",
            "operation_type": "select", "raw_sql": "SELECT 1", **_CRED,
        }))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"
        assert "Invalid database type" in result.error

    def test_sql_operation_mismatch_is_validation_error(self, logger, monitor):
        # operation_type=select but SQL is a DELETE -> validate_operation fails.
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd({
            "db_type": "postgresql", "host": "db", "database": "app",
            "operation_type": "select", "raw_sql": "DELETE FROM t", **_CRED,
        }))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"
        assert "SQL validation failed" in result.error

    def test_missing_credential_fails_closed(self, logger, monitor):
        # No credential_ref/__credentials -> resolve_effective_auth(require=True) raises
        # ValueError -> VALIDATION_ERROR.
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd({
            "db_type": "postgresql", "host": "db", "database": "app",
            "operation_type": "select", "raw_sql": "SELECT 1",
        }))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"


class TestFailurePaths:
    @patch(f"{_USECASE_MOD}.DatabaseEngineFactory.create_engine")
    def test_sqlalchemy_error_maps_to_database_error(self, mock_create, logger, monitor):
        engine, conn, _ = _mock_engine(columns=["id"], rows=[])
        conn.execute.side_effect = SQLAlchemyError("connection refused")
        mock_create.return_value = engine

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd({
            "db_type": "postgresql", "host": "db", "database": "app",
            "operation_type": "select", "raw_sql": "SELECT 1", **_CRED,
        }))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "DATABASE_ERROR"
        # engine.dispose still runs in the finally block even on failure.
        engine.dispose.assert_called_once()

    @patch(f"{_USECASE_MOD}.DatabaseEngineFactory.create_engine")
    def test_unexpected_error_maps_to_unknown_error(self, mock_create, logger, monitor):
        # A non-ValueError, non-SQLAlchemyError raised during engine creation.
        mock_create.side_effect = RuntimeError("boom")

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd({
            "db_type": "postgresql", "host": "db", "database": "app",
            "operation_type": "select", "raw_sql": "SELECT 1", **_CRED,
        }))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "UNKNOWN_ERROR"

    @patch(f"{_USECASE_MOD}.DatabaseEngineFactory.create_engine")
    def test_sqlalchemy_error_message_is_sanitized(self, mock_create, logger, monitor):
        engine, conn, _ = _mock_engine(columns=["id"], rows=[])
        conn.execute.side_effect = SQLAlchemyError(
            "failed for postgresql://admin:supersecret@db:5432/app"
        )
        mock_create.return_value = engine

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd({
            "db_type": "postgresql", "host": "db", "database": "app",
            "operation_type": "select", "raw_sql": "SELECT 1", **_CRED,
        }))

        assert "supersecret" not in result.error
        assert "***:***@" in result.error


class TestBuildConnectionConfig:
    def test_default_port_used_when_absent(self, logger, monitor):
        from app.layer1_domain.value_objects.database_type import DatabaseType
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        cfg = uc._build_connection_config(
            {"host": "db", "database": "app", **_CRED},
            DatabaseType.MYSQL, timeout=30,
        )
        assert cfg.port == 3306  # MySQL default from DatabaseEngineFactory.DEFAULT_PORTS

    def test_explicit_port_overrides_default(self, logger, monitor):
        from app.layer1_domain.value_objects.database_type import DatabaseType
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        cfg = uc._build_connection_config(
            {"host": "db", "database": "app", "port": 6543, **_CRED},
            DatabaseType.POSTGRESQL, timeout=45,
        )
        assert cfg.port == 6543
        assert cfg.statement_timeout == 45
        assert cfg.username == "u"
        assert cfg.password == "p"

    def test_ssl_enabled_coerced_from_string(self, logger, monitor):
        from app.layer1_domain.value_objects.database_type import DatabaseType
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        cfg = uc._build_connection_config(
            {"host": "db", "database": "app", "ssl_enabled": "true", **_CRED},
            DatabaseType.POSTGRESQL, timeout=30,
        )
        assert cfg.ssl_enabled is True


class TestScalarHelpers:
    def test_to_int_valid_and_fallback(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        assert uc._to_int("42", 0) == 42
        assert uc._to_int(None, 7) == 7          # None -> default
        assert uc._to_int("not-a-number", 9) == 9  # ValueError -> default
        assert uc._to_int([1, 2], 5) == 5          # TypeError -> default

    def test_to_bool_variants(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        assert uc._to_bool(True) is True
        assert uc._to_bool("yes") is True
        assert uc._to_bool("1") is True
        assert uc._to_bool("false") is False
        assert uc._to_bool(0) is False

    def test_sanitize_error_truncates_long(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        out = uc._sanitize_error("x" * 600)
        assert out.endswith("... [TRUNCATED]")
        assert len(out) == 500 + len("... [TRUNCATED]")

    def test_sanitize_error_hides_password(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        out = uc._sanitize_error("mysql://root:hunter2@host/db")
        assert "hunter2" not in out
        assert "***:***@" in out


class TestSchemas:
    def test_input_schema_has_required_keys(self):
        keys = [f["key"] for f in DATABASE_CONNECTION_INPUT_SCHEMA]
        for expected in ("db_type", "host", "port", "database", "operation_type",
                         "raw_sql", "max_rows", "timeout_seconds", "ssl_enabled"):
            assert expected in keys
        # T5 hard-remove: inline username/password fields are gone from the schema.
        assert "username" not in keys
        assert "password" not in keys

    def test_output_schema_has_result_keys(self):
        keys = [f["key"] for f in DATABASE_CONNECTION_OUTPUT_SCHEMA]
        for expected in ("success", "data", "row_count", "affected_rows",
                         "operation_type", "execution_time_ms", "error", "error_code"):
            assert expected in keys

    def test_defaults_are_sane_boundaries(self):
        assert DEFAULT_MAX_ROWS == 1000
        assert DEFAULT_TIMEOUT == 30
