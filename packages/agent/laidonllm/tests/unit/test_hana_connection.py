"""Tests for litellm/proxy/db/hana/connection.py (SA-2237).

Lives outside the litellm package for the same collection-import reason as
test_hana_config.py (see that file's docstring) — connection.py takes a
duck-typed config and lazily imports its driver, so it loads by file path
with no package needed.

In scope per the ticket's design diagram (connection.py's own docstring:
"the driver is imported lazily so this module can be imported (and its
validation/fail-fast paths unit-tested) without the driver installed").
The driver-absent path is exercised by forcing the import to fail, not by
relying on `hdbcli` being missing from the environment: `hdbcli>=2.29.23` is a
declared project dependency, so it IS importable in the project venv and any
test that assumed otherwise passed only under `uv run --no-project`.
Everything reaching a real HANA host (dbapi.connect actually connecting)
stays out of scope.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

import pytest

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "litellm", "proxy", "db", "hana")


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_BASE, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


connection = _load("hana_connection", "connection.py")
HanaConnectionManager = connection.HanaConnectionManager
HanaConnectionError = connection.HanaConnectionError


def _cfg(**overrides):
    base = dict(
        host="h",
        port=443,
        user="u",
        password="pw",
        encrypt=True,
        validate_cert=False,
        connect_timeout_ms=15000,
        schema=None,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


class _FakeCursor:
    def __init__(self, execute=None):
        self.closed = False
        self.executed = []
        self._execute = execute

    def execute(self, sql, *args):
        self.executed.append(sql)
        if self._execute:
            self._execute(sql)

    def fetchone(self):
        return (1,)

    def close(self):
        self.closed = True


class _FakeConn:
    def __init__(self, cursor_execute=None):
        self.closed = False
        self._cursor_execute = cursor_execute

    def cursor(self):
        return _FakeCursor(self._cursor_execute)

    def close(self):
        self.closed = True


# --- _import_dbapi: fail-fast with an install hint when the driver is absent -


def test_import_dbapi_raises_when_driver_absent(monkeypatch):
    """The driver-absent path, made independent of what is installed.

    Binding ``sys.modules["hdbcli"]`` to None makes ``from hdbcli import dbapi``
    raise ImportError even when the real package is present, so this asserts the
    behaviour of ``_import_dbapi`` rather than the shape of the environment.
    """
    monkeypatch.setitem(sys.modules, "hdbcli", None)
    with pytest.raises(HanaConnectionError, match="hdbcli"):
        connection._import_dbapi()


# --- connect(): wraps a driver connect failure ------------------------------


def test_connect_wraps_driver_failure(monkeypatch):
    fake_dbapi = types.SimpleNamespace(
        connect=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("network unreachable"))
    )
    monkeypatch.setattr(connection, "_import_dbapi", lambda: fake_dbapi)
    mgr = HanaConnectionManager(_cfg())
    with pytest.raises(HanaConnectionError, match="Failed to connect to SAP HANA"):
        mgr.connect()


# --- connect(): a SET SCHEMA failure closes the connection before raising ---


def test_connect_set_schema_failure_closes_connection_before_raising(monkeypatch):
    def _raise_on_set_schema(sql):
        if sql.startswith("SET SCHEMA"):
            raise RuntimeError("schema does not exist")

    fake_conn = _FakeConn(cursor_execute=_raise_on_set_schema)
    fake_dbapi = types.SimpleNamespace(connect=lambda **kwargs: fake_conn)
    monkeypatch.setattr(connection, "_import_dbapi", lambda: fake_dbapi)

    mgr = HanaConnectionManager(_cfg(schema="MYSCHEMA"))
    with pytest.raises(HanaConnectionError, match="SET SCHEMA"):
        mgr.connect()
    assert fake_conn.closed is True


def test_connect_without_schema_does_not_set_schema(monkeypatch):
    fake_conn = _FakeConn()
    fake_dbapi = types.SimpleNamespace(connect=lambda **kwargs: fake_conn)
    monkeypatch.setattr(connection, "_import_dbapi", lambda: fake_dbapi)

    mgr = HanaConnectionManager(_cfg(schema=None))
    got = mgr.connect()
    assert got is fake_conn
    assert fake_conn.closed is False


# --- ensure_connected(): caches the connection ------------------------------


def test_ensure_connected_caches_the_connection(monkeypatch):
    calls = []

    def _connect():
        calls.append(1)
        conn = _FakeConn()
        mgr._conn = conn  # real connect() caches as a side effect; mirror that here
        return conn

    mgr = HanaConnectionManager(_cfg())
    monkeypatch.setattr(mgr, "connect", _connect)

    first = mgr.ensure_connected()
    second = mgr.ensure_connected()
    assert first is second
    assert len(calls) == 1


# --- health_check(): True only for a row equal to 1 -------------------------


def test_health_check_true_for_row_equal_one(monkeypatch):
    mgr = HanaConnectionManager(_cfg())
    monkeypatch.setattr(mgr, "ensure_connected", lambda: _FakeConn())
    assert mgr.health_check() is True


def test_health_check_false_for_other_row(monkeypatch):
    class _WrongRowConn(_FakeConn):
        def cursor(self):
            cur = _FakeCursor()
            cur.fetchone = lambda: (0,)
            return cur

    mgr = HanaConnectionManager(_cfg())
    monkeypatch.setattr(mgr, "ensure_connected", lambda: _WrongRowConn())
    assert mgr.health_check() is False


def test_health_check_wraps_execution_failure(monkeypatch):
    class _RaisingConn(_FakeConn):
        def cursor(self):
            cur = _FakeCursor()

            def _boom(sql, *args):
                raise RuntimeError("connection reset")

            cur.execute = _boom
            return cur

    mgr = HanaConnectionManager(_cfg())
    monkeypatch.setattr(mgr, "ensure_connected", lambda: _RaisingConn())
    with pytest.raises(HanaConnectionError, match="health check failed"):
        mgr.health_check()


# --- cursor(): closes the cursor even when the body raises ------------------


def test_cursor_context_manager_closes_cursor_on_success(monkeypatch):
    fake_conn = _FakeConn()
    mgr = HanaConnectionManager(_cfg())
    monkeypatch.setattr(mgr, "ensure_connected", lambda: fake_conn)

    with mgr.cursor() as cur:
        pass
    assert cur.closed is True


def test_cursor_context_manager_closes_cursor_when_body_raises(monkeypatch):
    fake_conn = _FakeConn()
    mgr = HanaConnectionManager(_cfg())
    monkeypatch.setattr(mgr, "ensure_connected", lambda: fake_conn)

    held = {}
    with pytest.raises(ValueError):
        with mgr.cursor() as cur:
            held["cur"] = cur
            raise ValueError("boom")
    assert held["cur"].closed is True
