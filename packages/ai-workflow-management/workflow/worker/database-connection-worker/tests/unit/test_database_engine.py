"""Unit tests for DatabaseEngineFactory.

The URL / connect-args builders are pure functions (no DB) and get full coverage.
The connection-touching methods (test_connection, get_connection) are exercised with
the ``create_engine`` seam mocked — no live database. All calls are sync -> MagicMock.
Expectations are grounded in ``database_engine.py``.
"""

import urllib.parse
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.layer1_domain.value_objects.database_type import DatabaseType
from app.layer2_application.services.database_engine import (
    ConnectionConfig,
    ConnectionResult,
    DatabaseEngineFactory,
)

_ENGINE_MOD = "app.layer2_application.services.database_engine"


def _cfg(db_type=DatabaseType.POSTGRESQL, **kw):
    base = dict(
        db_type=db_type, host="dbhost", port=5432, database="mydb",
        username="user", password="pass",
    )
    base.update(kw)
    return ConnectionConfig(**base)


class TestBuildConnectionUrl:
    def test_postgresql_url(self):
        url = DatabaseEngineFactory._build_connection_url(_cfg(DatabaseType.POSTGRESQL))
        assert url == "postgresql+psycopg2://user:pass@dbhost:5432/mydb"

    def test_mysql_url_has_charset(self):
        url = DatabaseEngineFactory._build_connection_url(
            _cfg(DatabaseType.MYSQL, port=3306)
        )
        assert url == "mysql+pymysql://user:pass@dbhost:3306/mydb?charset=utf8mb4"

    def test_hana_url(self):
        url = DatabaseEngineFactory._build_connection_url(
            _cfg(DatabaseType.HANA, port=30015)
        )
        assert url == "hana+hdbcli://user:pass@dbhost:30015/mydb"

    def test_special_chars_are_url_encoded(self):
        # Password with '@' and ':' must be percent-encoded so the URL stays parseable.
        url = DatabaseEngineFactory._build_connection_url(
            _cfg(username="a@b", password="p:s@w/d", database="my db")
        )
        assert urllib.parse.quote_plus("a@b") in url
        assert urllib.parse.quote_plus("p:s@w/d") in url
        assert urllib.parse.quote_plus("my db") in url
        # Raw special chars must not leak into the authority section.
        assert "p:s@w/d" not in url


class TestBuildConnectArgs:
    def test_postgresql_statement_timeout_in_ms(self):
        args = DatabaseEngineFactory._build_connect_args(
            _cfg(DatabaseType.POSTGRESQL, statement_timeout=30)
        )
        assert args["options"] == "-c statement_timeout=30000"
        assert "sslmode" not in args  # ssl disabled by default

    def test_postgresql_ssl_require_with_ca(self):
        args = DatabaseEngineFactory._build_connect_args(
            _cfg(DatabaseType.POSTGRESQL, ssl_enabled=True, ssl_ca_cert="/tmp/ca.pem")
        )
        assert args["sslmode"] == "require"
        assert args["sslrootcert"] == "/tmp/ca.pem"

    def test_mysql_timeouts_and_ssl(self):
        args = DatabaseEngineFactory._build_connect_args(
            _cfg(DatabaseType.MYSQL, statement_timeout=15, ssl_enabled=True)
        )
        assert args["connect_timeout"] == 15
        assert args["read_timeout"] == 15
        assert args["write_timeout"] == 15
        assert args["ssl"]["ssl_mode"] == "REQUIRED"

    def test_hana_timeout_in_ms(self):
        args = DatabaseEngineFactory._build_connect_args(
            _cfg(DatabaseType.HANA, statement_timeout=20)
        )
        assert args["timeout"] == 20000


class TestDefaultPorts:
    def test_default_ports_map(self):
        assert DatabaseEngineFactory.DEFAULT_PORTS[DatabaseType.POSTGRESQL] == 5432
        assert DatabaseEngineFactory.DEFAULT_PORTS[DatabaseType.MYSQL] == 3306
        assert DatabaseEngineFactory.DEFAULT_PORTS[DatabaseType.HANA] == 30015


class TestCreateEngine:
    @patch(f"{_ENGINE_MOD}.create_engine")
    def test_create_engine_wires_pool_settings(self, mock_create):
        mock_create.return_value = MagicMock()
        cfg = _cfg(pool_size=7, max_overflow=3)
        DatabaseEngineFactory.create_engine(cfg)

        mock_create.assert_called_once()
        call = mock_create.call_args
        # First positional arg is the connection URL.
        assert call.args[0] == "postgresql+psycopg2://user:pass@dbhost:5432/mydb"
        assert call.kwargs["pool_size"] == 7
        assert call.kwargs["max_overflow"] == 3
        assert call.kwargs["pool_pre_ping"] is True


def _mock_engine_with_conn():
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value = conn
    return engine, conn


class TestTestConnection:
    @patch(f"{_ENGINE_MOD}.DatabaseEngineFactory.create_engine")
    def test_successful_connection_returns_version_and_latency(self, mock_create):
        engine, conn = _mock_engine_with_conn()
        # SELECT 1 then version() -> row[0]
        select1_result = MagicMock()
        select1_result.fetchone.return_value = (1,)
        version_result = MagicMock()
        version_result.fetchone.return_value = ("PostgreSQL 15.2",)
        conn.execute.side_effect = [select1_result, version_result]
        mock_create.return_value = engine

        res = DatabaseEngineFactory.test_connection(_cfg())

        assert isinstance(res, ConnectionResult)
        assert res.success is True
        assert res.server_version == "PostgreSQL 15.2"
        assert res.latency_ms is not None
        engine.dispose.assert_called_once()

    @patch(f"{_ENGINE_MOD}.DatabaseEngineFactory.create_engine")
    def test_sqlalchemy_error_returns_failure(self, mock_create):
        engine, conn = _mock_engine_with_conn()
        conn.execute.side_effect = SQLAlchemyError("could not connect")
        mock_create.return_value = engine

        res = DatabaseEngineFactory.test_connection(_cfg())

        assert res.success is False
        assert res.error is not None
        assert res.server_version is None

    @patch(f"{_ENGINE_MOD}.DatabaseEngineFactory.create_engine")
    def test_generic_error_returns_failure(self, mock_create):
        mock_create.side_effect = RuntimeError("dns failure for postgresql://u:secret@h/db")

        res = DatabaseEngineFactory.test_connection(_cfg())

        assert res.success is False
        # Error message is sanitized (password hidden).
        assert "secret" not in res.error

    @patch(f"{_ENGINE_MOD}.DatabaseEngineFactory.create_engine")
    def test_timeout_override_applied(self, mock_create):
        engine, conn = _mock_engine_with_conn()
        r = MagicMock()
        r.fetchone.return_value = (1,)
        conn.execute.return_value = r
        mock_create.return_value = engine
        cfg = _cfg()

        DatabaseEngineFactory.test_connection(cfg, timeout=99)

        assert cfg.statement_timeout == 99


class TestGetServerVersion:
    def test_mysql_version(self):
        conn = MagicMock()
        r = MagicMock()
        r.fetchone.return_value = ("8.0.32",)
        conn.execute.return_value = r
        v = DatabaseEngineFactory._get_server_version(conn, DatabaseType.MYSQL)
        assert v == "8.0.32"

    def test_none_row_returns_none(self):
        conn = MagicMock()
        r = MagicMock()
        r.fetchone.return_value = None
        conn.execute.return_value = r
        assert DatabaseEngineFactory._get_server_version(conn, DatabaseType.POSTGRESQL) is None

    def test_exception_swallowed_returns_none(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("boom")
        assert DatabaseEngineFactory._get_server_version(conn, DatabaseType.HANA) is None


class TestGetConnection:
    @patch(f"{_ENGINE_MOD}.DatabaseEngineFactory.create_engine")
    def test_context_manager_yields_conn_and_disposes(self, mock_create):
        engine, conn = _mock_engine_with_conn()
        mock_create.return_value = engine

        with DatabaseEngineFactory.get_connection(_cfg()) as c:
            assert c is conn

        engine.dispose.assert_called_once()


class TestSanitizeError:
    def test_hides_password(self):
        out = DatabaseEngineFactory._sanitize_error("mysql://root:hunter2@h/db")
        assert "hunter2" not in out
        assert "***:***@" in out

    def test_truncates_long(self):
        out = DatabaseEngineFactory._sanitize_error("y" * 700)
        assert out.endswith("... [TRUNCATED]")
        assert len(out) == 500 + len("... [TRUNCATED]")
