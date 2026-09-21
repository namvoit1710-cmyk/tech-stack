"""Tests for litellm/proxy/db/hana/config.py (SA-2237).

Lives OUTSIDE the litellm package on purpose: litellm/proxy/ has an
__init__.py, so a test placed under litellm/proxy/db/hana/ would be
collected as `litellm.proxy.db.hana.test_*` and import litellm/__init__.py,
which does an unguarded `import dotenv` and dies during collection in this
lightweight env. Loading the target by file path avoids importing the
package at all — config.py is stdlib-only precisely so this works.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "litellm", "proxy", "db", "hana")


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_BASE, filename))
    mod = importlib.util.module_from_spec(spec)
    # Must register in sys.modules BEFORE exec_module: @dataclass resolves
    # sys.modules[cls.__module__] while building the class, and an
    # unregistered module name resolves to None -> AttributeError.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


config = _load("hana_config", "config.py")

_ENV_VARS = (
    "DB_BACKEND",
    "HANA_HOST",
    "HANA_PORT",
    "HANA_USER",
    "HANA_PASSWORD",
    "HANA_ENCRYPT",
    "HANA_VALIDATE_CERT",
    "HANA_SCHEMA",
    "HANA_CONNECT_TIMEOUT_MS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# --- backend selection --------------------------------------------------


def test_default_backend_is_prisma():
    assert config.get_db_backend() == "prisma"
    assert config.is_hana_backend() is False


def test_hana_backend_selected(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "HANA")
    assert config.get_db_backend() == "hana"
    assert config.is_hana_backend() is True


def test_unsupported_backend_raises(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    with pytest.raises(ValueError, match="not supported"):
        config.get_db_backend()


# --- HanaConfig.from_env fail-fast ---------------------------------------


def test_from_env_missing_vars_raises():
    with pytest.raises(config.HanaConfigError, match="HANA_HOST"):
        config.HanaConfig.from_env()


def test_from_env_builds_config(monkeypatch):
    monkeypatch.setenv("HANA_HOST", "h")
    monkeypatch.setenv("HANA_USER", "u")
    monkeypatch.setenv("HANA_PASSWORD", "SUPER-SECRET-PW")
    cfg = config.HanaConfig.from_env()
    assert cfg.host == "h"
    assert cfg.user == "u"
    assert cfg.password == "SUPER-SECRET-PW"
    assert cfg.port == 443
    assert cfg.connect_timeout_ms == 15000


def test_from_env_invalid_port_raises(monkeypatch):
    monkeypatch.setenv("HANA_HOST", "h")
    monkeypatch.setenv("HANA_USER", "u")
    monkeypatch.setenv("HANA_PASSWORD", "pw")
    monkeypatch.setenv("HANA_PORT", "not-a-number")
    with pytest.raises(config.HanaConfigError, match="HANA_PORT"):
        config.HanaConfig.from_env()


# --- R2: password must never leak through repr()/str() -------------------


def _make_cfg() -> "config.HanaConfig":
    return config.HanaConfig(host="h", port=443, user="u", password="SUPER-SECRET-PW")


def test_safe_repr_omits_password():
    cfg = _make_cfg()
    assert "SUPER-SECRET-PW" not in cfg.safe_repr()
    assert "password" not in cfg.safe_repr()


def test_repr_does_not_leak_password():
    cfg = _make_cfg()
    assert "SUPER-SECRET-PW" not in repr(cfg)
    assert repr(cfg) == cfg.safe_repr()


def test_str_does_not_leak_password():
    cfg = _make_cfg()
    assert "SUPER-SECRET-PW" not in str(cfg)
    assert str(cfg) == cfg.safe_repr()
