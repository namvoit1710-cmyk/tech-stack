"""T5 — db worker takes username/password only from a vault basic credential (F11/F14).

After the inline-secret hard-remove, a credential_ref + __credentials map is the only
source of username/password; with no resolvable credential the worker fails closed.
"""

import pytest

from app.layer1_domain.value_objects.database_type import DatabaseType
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
)


class _Noop:
    def __getattr__(self, _):
        return lambda *a, **k: None


def _uc() -> ExecuteTaskUseCase:
    return ExecuteTaskUseCase(logger=_Noop(), monitor=_Noop())


def test_takes_credentials_from_vault_basic():
    inputs = {
        "host": "db", "database": "app",
        "credential_ref": "c1",
        "__credentials": {"c1": {"type": "basic",
                                 "config": {"username": "vault_user", "password": "vault_pw"}}},
    }
    cfg = _uc()._build_connection_config(inputs, DatabaseType.POSTGRESQL, timeout=30)
    assert cfg.username == "vault_user"
    assert cfg.password == "vault_pw"


def test_fails_closed_without_credential():
    inputs = {"host": "db", "database": "app"}
    with pytest.raises(ValueError):
        _uc()._build_connection_config(inputs, DatabaseType.POSTGRESQL, timeout=30)


# --- SA-1564: host/port/database derived from a generic-database credential ---

_DB_CRED = {"c1": {"type": "json", "config": {
    "host": "vaultdb.example.com", "port": 6543, "database": "vault_db",
    "username": "vu", "password": "vp"}}}


def test_derives_host_port_database_from_credential():
    """A generic-database credential fully configures the connection — no inline needed."""
    inputs = {"credential_ref": "c1", "__credentials": _DB_CRED}
    cfg = _uc()._build_connection_config(inputs, DatabaseType.POSTGRESQL, timeout=30)
    assert cfg.host == "vaultdb.example.com"
    assert cfg.port == 6543
    assert cfg.database == "vault_db"
    assert cfg.username == "vu" and cfg.password == "vp"


def test_inline_overrides_credential_connection():
    """Inline host/port/database win over the credential's values."""
    inputs = {"host": "inline-host", "port": 1111, "database": "inline_db",
              "credential_ref": "c1", "__credentials": _DB_CRED}
    cfg = _uc()._build_connection_config(inputs, DatabaseType.POSTGRESQL, timeout=30)
    assert cfg.host == "inline-host"
    assert cfg.port == 1111
    assert cfg.database == "inline_db"


def test_port_defaults_when_neither_inline_nor_credential():
    """Port falls back to the db-type default; host/database come from the credential."""
    cred = {"c1": {"type": "json", "config": {
        "host": "h", "database": "d", "username": "u", "password": "p"}}}
    inputs = {"credential_ref": "c1", "__credentials": cred}
    cfg = _uc()._build_connection_config(inputs, DatabaseType.POSTGRESQL, timeout=30)
    assert cfg.port == 5432  # PostgreSQL default


def test_missing_host_even_after_credential_raises():
    """A login-only (basic) credential with no host → host still required, clear error."""
    cred = {"c1": {"type": "basic", "config": {"username": "u", "password": "p"}}}
    inputs = {"database": "app", "credential_ref": "c1", "__credentials": cred}
    with pytest.raises(ValueError, match="host"):
        _uc()._build_connection_config(inputs, DatabaseType.POSTGRESQL, timeout=30)


def test_missing_database_even_after_credential_raises():
    cred = {"c1": {"type": "basic", "config": {"username": "u", "password": "p"}}}
    inputs = {"host": "db", "credential_ref": "c1", "__credentials": cred}
    with pytest.raises(ValueError, match="database"):
        _uc()._build_connection_config(inputs, DatabaseType.POSTGRESQL, timeout=30)


def test_host_and_database_fields_are_optional_in_schema():
    """SA-1564: host/database no longer carry a 'required' rule (optional overrides)."""
    from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
        DATABASE_CONNECTION_INPUT_SCHEMA,
    )
    by_key = {f["key"]: f for f in DATABASE_CONNECTION_INPUT_SCHEMA}
    for key in ("host", "database"):
        rules = (by_key[key].get("fieldConfig", {}).get("rules") or [])
        methods = [r.get("method") for r in rules]
        assert "required" not in methods, f"{key} should be optional"
