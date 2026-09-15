"""Unit tests for the worker's design-time dynamic functions (app/functions.py).

These are the async handlers exposed for connection testing and schema discovery.
The DB is mocked at the ``DatabaseEngineFactory`` seam in the functions module — no
live database. The handlers are ``async def`` so tests await them (asyncio); the DB
collaborators themselves are sync -> MagicMock. Expectations grounded in functions.py.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.functions import (
    test_connection as run_test_connection,  # alias: bare `test_*` symbol would be
    list_tables,                              # mis-collected by pytest as a test node.
    get_table_columns,
    DATABASE_WORKER_FUNCTIONS,
)
from app.layer2_application.services.database_engine import ConnectionResult

_FN_MOD = "app.functions"

pytestmark = pytest.mark.asyncio


def _mock_engine(*, rows=None):
    result = MagicMock()
    result.fetchall.return_value = rows or []
    conn = MagicMock()
    conn.execute.return_value = result
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value = conn
    return engine, conn


class TestTestConnectionFn:
    @patch(f"{_FN_MOD}.DatabaseEngineFactory.test_connection")
    async def test_happy_path_maps_result(self, mock_test):
        mock_test.return_value = ConnectionResult(
            success=True, server_version="PG 15", latency_ms=1.2
        )
        out = await run_test_connection({
            "db_type": "postgresql", "host": "h", "database": "d", "username": "u",
        })
        assert out["success"] is True
        assert out["server_version"] == "PG 15"
        assert out["latency_ms"] == 1.2

    async def test_missing_db_type_rejected(self):
        out = await run_test_connection({"host": "h", "database": "d", "username": "u"})
        assert out["success"] is False
        assert "db_type" in out["error"]

    async def test_missing_host_rejected(self):
        out = await run_test_connection({"db_type": "mysql", "database": "d", "username": "u"})
        assert out["success"] is False
        assert "host" in out["error"]

    async def test_missing_username_rejected(self):
        out = await run_test_connection({"db_type": "mysql", "host": "h", "database": "d"})
        assert out["success"] is False
        assert "username" in out["error"]

    async def test_invalid_db_type_rejected(self):
        out = await run_test_connection({
            "db_type": "oracle", "host": "h", "database": "d", "username": "u",
        })
        assert out["success"] is False
        assert "Invalid database type" in out["error"]

    @patch(f"{_FN_MOD}.DatabaseEngineFactory.test_connection")
    async def test_default_port_used_when_absent(self, mock_test):
        mock_test.return_value = ConnectionResult(success=True)
        await run_test_connection({
            "db_type": "mysql", "host": "h", "database": "d", "username": "u",
        })
        # ConnectionConfig passed to test_connection should carry the MySQL default port.
        cfg = mock_test.call_args.args[0]
        assert cfg.port == 3306


class TestListTablesFn:
    @patch(f"{_FN_MOD}.DatabaseEngineFactory.create_engine")
    async def test_lists_tables_postgres(self, mock_create):
        engine, _ = _mock_engine(rows=[("public", "users"), ("public", "orders")])
        mock_create.return_value = engine

        out = await list_tables({
            "db_type": "postgresql", "host": "h", "database": "d", "username": "u",
        })
        assert out["success"] is True
        assert out["count"] == 2
        names = [t["table_name"] for t in out["tables"]]
        assert names == ["users", "orders"]
        assert out["tables"][0]["full_name"] == "public.users"
        engine.dispose.assert_called_once()

    async def test_missing_connection_params_rejected(self):
        out = await list_tables({"db_type": "postgresql", "host": "h"})  # no db/user
        assert out["success"] is False
        assert out["tables"] == []

    async def test_invalid_db_type_rejected(self):
        out = await list_tables({
            "db_type": "nope", "host": "h", "database": "d", "username": "u",
        })
        assert out["success"] is False
        assert "Invalid database type" in out["error"]

    @patch(f"{_FN_MOD}.DatabaseEngineFactory.create_engine")
    async def test_query_exception_sanitized(self, mock_create):
        mock_create.side_effect = Exception("fail postgresql://u:topsecret@h/d")
        out = await list_tables({
            "db_type": "postgresql", "host": "h", "database": "d", "username": "u",
        })
        assert out["success"] is False
        assert "topsecret" not in out["error"]


class TestGetTableColumnsFn:
    @patch(f"{_FN_MOD}.DatabaseEngineFactory.create_engine")
    async def test_returns_columns(self, mock_create):
        engine, _ = _mock_engine(rows=[
            ("id", "integer", "NO", None),
            ("name", "text", "YES", None),
        ])
        mock_create.return_value = engine

        out = await get_table_columns({
            "db_type": "postgresql", "host": "h", "database": "d", "username": "u",
            "table_name": "users",
        })
        assert out["success"] is True
        assert out["count"] == 2
        assert out["columns"][0]["name"] == "id"
        assert out["columns"][0]["nullable"] is False
        assert out["columns"][1]["nullable"] is True

    async def test_missing_table_name_rejected(self):
        out = await get_table_columns({
            "db_type": "postgresql", "host": "h", "database": "d", "username": "u",
        })
        assert out["success"] is False
        assert "table_name" in out["error"]

    async def test_missing_connection_params_rejected(self):
        out = await get_table_columns({"table_name": "t", "db_type": "postgresql"})
        assert out["success"] is False
        assert out["columns"] == []


class TestFunctionDefinitions:
    async def test_three_functions_registered(self):
        names = {f.name for f in DATABASE_WORKER_FUNCTIONS}
        assert names == {"test_connection", "list_tables", "get_table_columns"}
