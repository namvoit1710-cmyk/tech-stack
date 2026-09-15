"""Unit tests for HanaConnectionManager.

All tests mock ``hdbcli.dbapi`` so no real HANA instance is needed.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.layer4_frameworks.persistence.hana.hana_connection_manager import (
    HanaConnectionManager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides) -> SimpleNamespace:
    defaults = {
        "HANA_HOST": "localhost",
        "HANA_PORT": 30015,
        "HANA_USERNAME": "SYSTEM",
        "HANA_PASSWORD": "secret",
        "HANA_SCHEMA": "TEST_SCHEMA",
        "HANA_ENCRYPT": "false",
        "HANA_SSL_CERT": "",
        "HANA_POOL_SIZE": 2,
        "HANA_MAX_OVERFLOW": 1,
        "HANA_POOL_RECYCLE": 3600,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_connection():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


# ---------------------------------------------------------------------------
# _bind_params tests
# ---------------------------------------------------------------------------

class TestBindParams:
    """Parameter translation from :p0/:p1 to positional ?."""

    def test_translates_named_to_positional(self):
        sql = "SELECT * FROM T WHERE A = :p0 AND B = :p1"
        params = ("val_a", "val_b")
        new_sql, new_params = HanaConnectionManager._bind_params(sql, params)

        assert new_sql == "SELECT * FROM T WHERE A = ? AND B = ?"
        assert new_params == ("val_a", "val_b")

    def test_noop_without_named_placeholders(self):
        sql = "SELECT * FROM T"
        params = ()
        new_sql, new_params = HanaConnectionManager._bind_params(sql, params)

        assert new_sql == sql
        assert new_params == params

    def test_reorders_params_by_index(self):
        sql = "UPDATE T SET A = :p1 WHERE ID = :p0"
        params = ("id_val", "a_val")
        new_sql, new_params = HanaConnectionManager._bind_params(sql, params)

        assert new_sql == "UPDATE T SET A = ? WHERE ID = ?"
        assert new_params == ("a_val", "id_val")

    def test_handles_out_of_range_index(self):
        sql = "SELECT * FROM T WHERE A = :p0 AND B = :p5"
        params = ("only_one",)
        new_sql, new_params = HanaConnectionManager._bind_params(sql, params)

        assert new_sql == "SELECT * FROM T WHERE A = ? AND B = ?"
        assert new_params == ("only_one", None)

    def test_handles_duplicate_placeholders(self):
        sql = "SELECT * FROM T WHERE A = :p0 OR B = :p0"
        params = ("val",)
        new_sql, new_params = HanaConnectionManager._bind_params(sql, params)

        assert new_sql == "SELECT * FROM T WHERE A = ? OR B = ?"
        assert new_params == ("val", "val")


# ---------------------------------------------------------------------------
# execute_query tests
# ---------------------------------------------------------------------------

class TestExecuteQuery:

    @patch("app.layer4_frameworks.persistence.hana.hana_connection_manager.HanaConnectionManager._create_connection")
    def test_returns_list_of_dicts_with_lowercased_columns(self, mock_create):
        conn, cursor = _mock_connection()
        mock_create.return_value = conn
        cursor.description = [("USER_ID",), ("NAME",)]
        cursor.fetchall.return_value = [("u1", "Alice"), ("u2", "Bob")]

        mgr = HanaConnectionManager(_make_settings())
        mgr.initialize()

        result = mgr.execute_query("SELECT USER_ID, NAME FROM T")

        assert result == [
            {"user_id": "u1", "name": "Alice"},
            {"user_id": "u2", "name": "Bob"},
        ]

    @patch("app.layer4_frameworks.persistence.hana.hana_connection_manager.HanaConnectionManager._create_connection")
    def test_returns_empty_list_when_no_description(self, mock_create):
        conn, cursor = _mock_connection()
        mock_create.return_value = conn
        cursor.description = None

        mgr = HanaConnectionManager(_make_settings())
        mgr.initialize()

        result = mgr.execute_query("CALL SOME_PROC()")
        assert result == []

    @patch("app.layer4_frameworks.persistence.hana.hana_connection_manager.HanaConnectionManager._create_connection")
    def test_discards_connection_on_error(self, mock_create):
        conn, cursor = _mock_connection()
        mock_create.return_value = conn
        cursor.execute.side_effect = RuntimeError("DB error")

        mgr = HanaConnectionManager(_make_settings())
        mgr.initialize()

        with pytest.raises(RuntimeError, match="DB error"):
            mgr.execute_query("BAD SQL")

        conn.close.assert_called()


# ---------------------------------------------------------------------------
# execute_write tests
# ---------------------------------------------------------------------------

class TestExecuteWrite:

    @patch("app.layer4_frameworks.persistence.hana.hana_connection_manager.HanaConnectionManager._create_connection")
    def test_commits_on_success(self, mock_create):
        conn, cursor = _mock_connection()
        mock_create.return_value = conn

        mgr = HanaConnectionManager(_make_settings())
        mgr.initialize()

        mgr.execute_write("INSERT INTO T VALUES (:p0)", ("val",))

        conn.commit.assert_called_once()

    @patch("app.layer4_frameworks.persistence.hana.hana_connection_manager.HanaConnectionManager._create_connection")
    def test_rollback_on_error(self, mock_create):
        conn, cursor = _mock_connection()
        mock_create.return_value = conn
        cursor.execute.side_effect = RuntimeError("Write failed")

        mgr = HanaConnectionManager(_make_settings())
        mgr.initialize()

        with pytest.raises(RuntimeError, match="Write failed"):
            mgr.execute_write("BAD INSERT")

        conn.rollback.assert_called_once()
        conn.close.assert_called()


# ---------------------------------------------------------------------------
# Pool lifecycle tests
# ---------------------------------------------------------------------------

class TestPoolLifecycle:

    @patch("app.layer4_frameworks.persistence.hana.hana_connection_manager.HanaConnectionManager._create_connection")
    def test_initialize_pre_populates_pool(self, mock_create):
        conn, _ = _mock_connection()
        mock_create.return_value = conn
        settings = _make_settings(HANA_POOL_SIZE=3)

        mgr = HanaConnectionManager(settings)
        mgr.initialize()

        assert mgr._pool.qsize() == 3

    @patch("app.layer4_frameworks.persistence.hana.hana_connection_manager.HanaConnectionManager._create_connection")
    def test_close_drains_pool(self, mock_create):
        conns = [_mock_connection()[0] for _ in range(2)]
        mock_create.side_effect = conns
        settings = _make_settings(HANA_POOL_SIZE=2)

        mgr = HanaConnectionManager(settings)
        mgr.initialize()
        assert mgr._pool.qsize() == 2

        mgr.close()
        assert mgr._pool.qsize() == 0
        for c in conns:
            c.close.assert_called()

    @patch("app.layer4_frameworks.persistence.hana.hana_connection_manager.HanaConnectionManager._create_connection")
    def test_stale_connections_recycled(self, mock_create):
        conn1, cursor1 = _mock_connection()
        conn2, cursor2 = _mock_connection()
        mock_create.side_effect = [conn1, conn2]

        settings = _make_settings(HANA_POOL_SIZE=1, HANA_POOL_RECYCLE=0)
        mgr = HanaConnectionManager(settings)
        mgr.initialize()

        cursor2.description = [("ID",)]
        cursor2.fetchall.return_value = [("x",)]

        result = mgr.execute_query("SELECT ID FROM T")
        assert result == [{"id": "x"}]
        conn1.close.assert_called()


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------

class TestMigrations:

    @staticmethod
    def _write_test_sql(tmp_path):
        """Write a minimal SQL file so the migration runner has something to load."""
        sql_file = tmp_path / "001_test.sql"
        sql_file.write_text('CREATE COLUMN TABLE "TEST_T" ("ID" NVARCHAR(64) NOT NULL PRIMARY KEY)')
        return tmp_path

    @patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR")
    @patch("app.layer4_frameworks.persistence.hana.hana_connection_manager.HanaConnectionManager._create_connection")
    def test_migrate_skips_existing_tables(self, mock_create, mock_dir, tmp_path):
        self._write_test_sql(tmp_path)
        mock_dir.__class__ = type(tmp_path)
        mock_dir.is_dir.return_value = True
        mock_dir.glob.return_value = sorted(tmp_path.glob("*.sql"))

        conn, _ = _mock_connection()
        mock_create.return_value = conn

        mgr = HanaConnectionManager(_make_settings())
        mgr.initialize()

        class HanaError(Exception):
            def __init__(self):
                self.errorcode = 288
                super().__init__("duplicate table name")

        from app.layer4_frameworks.persistence.hana.migrations import (
            _LEDGER_TABLE,
            migrate,
        )

        # Duplicate-table error on the migration DDL; the applied-migrations
        # ledger writes (CREATE / UPSERT) succeed so the runner can record
        # progress.
        def _raise_dup(sql, *args, **kwargs):
            if _LEDGER_TABLE in sql:
                return None
            raise HanaError()

        mgr.execute_write = MagicMock(side_effect=_raise_dup)
        mgr.execute_query = MagicMock(return_value=[])

        # Should not raise
        migrate(mgr)

    @patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR")
    @patch("app.layer4_frameworks.persistence.hana.hana_connection_manager.HanaConnectionManager._create_connection")
    def test_migrate_raises_on_unexpected_error(self, mock_create, mock_dir, tmp_path):
        self._write_test_sql(tmp_path)
        mock_dir.is_dir.return_value = True
        mock_dir.glob.return_value = sorted(tmp_path.glob("*.sql"))

        conn, _ = _mock_connection()
        mock_create.return_value = conn

        mgr = HanaConnectionManager(_make_settings())
        mgr.initialize()

        from app.layer4_frameworks.persistence.hana.migrations import (
            _LEDGER_TABLE,
            migrate,
        )

        # The ledger bootstrap succeeds; the migration DDL raises the
        # unexpected error, which must propagate.
        def _raise_unexpected(sql, *args, **kwargs):
            if _LEDGER_TABLE in sql:
                return None
            raise RuntimeError("unexpected")

        mgr.execute_write = MagicMock(side_effect=_raise_unexpected)
        mgr.execute_query = MagicMock(return_value=[])

        with pytest.raises(RuntimeError, match="unexpected"):
            migrate(mgr)
