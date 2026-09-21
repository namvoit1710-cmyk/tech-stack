"""Tests for the pure-logic helpers in litellm/proxy/db/hana/repository.py (SA-2237).

Lives outside the litellm package for the same collection-import reason as
test_hana_config.py (see that file's docstring). repository.py additionally
does `from .records import HanaRecord` (a package-relative import), so a
naive file-path load fails with "attempted relative import with no known
parent package". Fix: synthesize a throwaway parent package named "hana"
carrying __path__, load records.py as hana.records first, then load
repository.py as hana.repository so its relative import resolves.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import re
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


_pkg = types.ModuleType("hana")
_pkg.__path__ = [_BASE]
sys.modules["hana"] = _pkg
_load("hana.records", "records.py")
repository = _load("hana.repository", "repository.py")

TableSpec = repository.TableSpec
HanaRepository = repository.HanaRepository
_q = repository._q
_is_missing_table = repository._is_missing_table
_is_optional_table = repository._is_optional_table
_invalid_column_name = repository._invalid_column_name


def _repo(**spec_kwargs) -> "HanaRepository":
    spec = TableSpec(name="LiteLLM_VerificationToken", pk="token", **spec_kwargs)
    return HanaRepository(mgr=None, spec=spec)


# --- R1: _q is the only defense against identifier injection --------------


def test_q_quotes_a_plain_identifier():
    assert _q("token") == '"token"'


def test_q_doubles_an_embedded_quote():
    assert _q('a"b') == '"a""b"'


def test_q_doubling_prevents_early_termination_of_the_identifier():
    """An embedded quote must not be able to close the quoted identifier early.

    If _q ever stopped doubling, `a"b` would render as `"a"b"` — three tokens
    to a SQL parser, not one escaped identifier. Doubling keeps it a single
    quoted identifier: exactly one pair of un-doubled quote characters.
    """
    quoted = _q('a"b')
    # Splitting on the doubled-quote escape must leave nothing but the
    # identifier's own characters (no stray unescaped quote sneaks through).
    body = quoted[1:-1]
    assert body.replace('""', "") == "ab"
    assert quoted.count('"') == 4  # 2 wrapping quotes + 2 for the doubled one


# --- R4: _is_missing_table matches on message substrings, not table -------


@pytest.mark.parametrize(
    "message",
    [
        "invalid table name: LITELLM_FOO at line 1",
        "cannot find table LITELLM_FOO",
        "INVALID TABLE NAME: LITELLM_FOO",  # case-insensitive
    ],
)
def test_is_missing_table_matches_known_messages(message):
    assert _is_missing_table(Exception(message)) is True


def test_is_missing_table_false_for_unrelated_error():
    assert _is_missing_table(Exception("connection reset by peer")) is False


# --- _is_optional_table -----------------------------------------------------


def test_is_optional_table_true_for_audit_log():
    assert _is_optional_table("LiteLLM_AuditLog") is True


def test_is_optional_table_false_for_core_table():
    assert _is_optional_table("LiteLLM_VerificationToken") is False


# --- _invalid_column_name ----------------------------------------------------


def test_invalid_column_name_extracts_and_lowercases():
    exc = Exception("invalid column name: CREATED_AT: line 1 col 5")
    assert _invalid_column_name(exc) == "created_at"


def test_invalid_column_name_none_for_unrelated_error():
    assert _invalid_column_name(Exception("connection reset by peer")) is None


# --- pure helpers exercised via HanaRepository(mgr=None, ...) ---------------


def test_build_where_equality_and_none():
    repo = _repo()
    sql, params = repo._build_where({"token": "sk-1", "team_id": None})
    assert sql == '"token" = ? AND "team_id" IS NULL'
    assert params == ["sk-1"]


def test_build_where_in_operator():
    repo = _repo()
    sql, params = repo._build_where({"team_id": {"in": ["a", "b"]}})
    assert sql == '"team_id" IN (?, ?)'
    assert params == ["a", "b"]


# --- SA-2296: {"mode": "insensitive"} must not be silently dropped ---------


def test_build_where_equals_with_insensitive_mode_wraps_upper():
    """Prisma's case-insensitive email lookup (login_utils.py) must translate
    to a HANA-safe UPPER() comparison instead of a plain (collation-dependent)
    equality, since dropping the hint made a mixed-case email fail to match."""
    repo = _repo()
    sql, params = repo._build_where({"user_email": {"equals": "Foo@Example.com", "mode": "insensitive"}})
    assert sql == 'UPPER("user_email") = UPPER(?)'
    assert params == ["Foo@Example.com"]


def test_build_where_equals_without_mode_stays_case_sensitive():
    repo = _repo()
    sql, params = repo._build_where({"user_email": {"equals": "foo@example.com"}})
    assert sql == '"user_email" = ?'
    assert params == ["foo@example.com"]


def test_build_where_mode_insensitive_ignored_for_non_equals_ops():
    repo = _repo()
    sql, params = repo._build_where({"team_id": {"in": ["a", "b"], "mode": "insensitive"}})
    assert sql == '"team_id" IN (?, ?)'
    assert params == ["a", "b"]


def test_encode_json_column_serializes_dict():
    repo = _repo(json_cols=frozenset({"metadata", "models"}))
    out = repo._encode({"metadata": {"a": 1}, "models": None, "x": 5})
    assert out == {"metadata": '{"a": 1}', "models": None, "x": 5}


def test_decode_row_null_json_cell_defaults_per_prisma_shape():
    repo = _repo(json_cols=frozenset({"models", "metadata"}), bool_cols=frozenset({"blocked"}))
    rec = repo._decode_row(["models", "metadata", "blocked"], [None, None, 1])
    assert rec == {"models": [], "metadata": {}, "blocked": True}


def test_decode_row_bool_column_coerces_to_bool():
    repo = _repo(bool_cols=frozenset({"blocked"}))
    rec = repo._decode_row(["blocked"], [1])
    assert rec == {"blocked": True}


# --- SA-2310: _q must guard every SQL identifier call site, not just itself -
#
# _q (R1 above) is correct and mutation-verified as a function, but nothing
# asserted that create()/group_by() actually route caller-supplied column
# names through it. These record the exact SQL handed to the fake cursor so a
# regression that drops _q at any one of the three call sites (INSERT column
# list, GROUP BY select list, GROUP BY clause) fails a test.


class _RecordingCursor:
    """Fake HANA cursor: records the exact (sql, params) passed to execute()."""

    def __init__(self, description=()):
        self.calls: list = []
        self.description = description

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return []

    def close(self):
        pass


class _RecordingMgr:
    def __init__(self, description=()):
        self.cur = _RecordingCursor(description=description)

    def cursor(self):
        @contextlib.contextmanager
        def _c():
            yield self.cur

        return _c()


def test_create_quotes_insert_column_names_including_an_embedded_quote():
    """INSERT column list (repository.py create()) must quote every caller-
    supplied column name via _q — a name containing a double quote must
    survive as one quoted identifier, not break out of it."""
    mgr = _RecordingMgr()
    repo = HanaRepository(mgr=mgr, spec=TableSpec(name="LiteLLM_Config", pk="id"))
    repo.create({'a"b': "x", "created_at": "t1", "updated_at": "t2"})
    sql, params = mgr.cur.calls[0]
    assert sql == ('INSERT INTO "LiteLLM_Config" ("a""b", "created_at", "updated_at") VALUES (?, ?, ?)')
    assert params == ["x", "t1", "t2"]


# --- SA-2324: LiteLLM_SearchToolsTable round-trips through the new TableSpec -
#
# Done-when asks to create a Search Tool and read it back in the Admin UI; this
# sandbox has no live HANA or browser to drive that end-to-end, so this proves
# the same path the UI action exercises at the repository layer: create() then
# find_unique() against the exact TableSpec added to client.py's MODEL_SPECS.


class _FakeSearchToolsCursor:
    """Minimal in-memory single-table HANA stand-in: enough SQL parsing to
    execute the repository's own INSERT and single-pk-equality SELECT."""

    def __init__(self, rows: dict):
        self.rows = rows
        self.description = None
        self._result: list = []

    def execute(self, sql, params=None):
        params = params or []
        if sql.startswith("INSERT INTO"):
            cols = [c.strip().strip('"') for c in re.search(r"\((.*?)\) VALUES", sql, re.S).group(1).split(",")]
            row = dict(zip(cols, params))
            self.rows[row["search_tool_id"]] = row
        else:  # SELECT * FROM "..." WHERE "search_tool_id" = ?
            m = re.search(r'WHERE "(\w+)" = \?', sql)
            if m:
                self._result = [r for r in self.rows.values() if r.get(m.group(1)) == params[0]]
            else:
                self._result = list(self.rows.values())
            self.description = [(c,) for c in self._result[0].keys()] if self._result else []

    def fetchall(self):
        return [tuple(r.values()) for r in self._result]

    def close(self):
        pass


class _FakeSearchToolsMgr:
    def __init__(self):
        self.rows: dict = {}

    def cursor(self):
        @contextlib.contextmanager
        def _c():
            yield _FakeSearchToolsCursor(self.rows)

        return _c()


def test_search_tools_table_create_and_read_back_round_trip():
    spec = TableSpec(
        name="LiteLLM_SearchToolsTable",
        pk="search_tool_id",
        json_cols=frozenset({"litellm_params", "search_tool_info"}),
        auto_uuid=frozenset({"search_tool_id"}),
    )
    repo = HanaRepository(mgr=_FakeSearchToolsMgr(), spec=spec)

    created = repo.create({"search_tool_name": "web-search", "litellm_params": {"engine": "bing"}})
    assert created["search_tool_name"] == "web-search"
    tool_id = created["search_tool_id"]
    assert tool_id  # auto_uuid generated one since the caller didn't supply it

    fetched = repo.find_unique(where={"search_tool_id": tool_id})
    assert fetched is not None
    assert fetched["search_tool_name"] == "web-search"
    assert fetched["litellm_params"] == {"engine": "bing"}  # NCLOB JSON round-trips through decode


# --- SA-2332: LiteLLM_SSOConfig / LiteLLM_UISettings round-trip through the ---
# TableSpecs added to client.py's MODEL_SPECS.
#
# Both callers (proxy_setting_endpoints.py) upsert with an explicit, fixed id
# ("sso_config" / "ui_settings") and then read back with a plain dict()/membership
# check on the JSON column - so this proves, at the repository layer: (1) the
# fixed id survives create as-is (auto_uuid must NOT touch it), and (2) the JSON
# column decodes back to a dict, not a raw NCLOB string (json_cols is required).


class _FakeSingletonCursor:
    """Minimal in-memory single-table HANA stand-in, generalized over the pk
    column name so it serves both the SSOConfig and UISettings specs."""

    def __init__(self, rows: dict, pk: str):
        self.rows = rows
        self.pk = pk
        self.description = None
        self._result: list = []

    def execute(self, sql, params=None):
        params = params or []
        if sql.startswith("INSERT INTO"):
            cols = [c.strip().strip('"') for c in re.search(r"\((.*?)\) VALUES", sql, re.S).group(1).split(",")]
            row = dict(zip(cols, params))
            self.rows[row[self.pk]] = row
        elif sql.startswith("UPDATE"):
            m = re.search(r'WHERE "(\w+)" = \?', sql)
            key = params[-1] if m else None
            row = self.rows.get(key, {})
            frags = re.findall(r'"(\w+)" = \?', sql.split(" WHERE ")[0])
            for col, val in zip(frags, params):
                row[col] = val
            self.rows[key] = row
        else:  # SELECT * FROM "..." WHERE "<pk>" = ?
            m = re.search(r'WHERE "(\w+)" = \?', sql)
            if m:
                self._result = [r for r in self.rows.values() if r.get(m.group(1)) == params[0]]
            else:
                self._result = list(self.rows.values())
            self.description = [(c,) for c in self._result[0].keys()] if self._result else []

    def fetchall(self):
        return [tuple(r.values()) for r in self._result]

    def close(self):
        pass


class _FakeSingletonMgr:
    def __init__(self, pk: str):
        self.pk = pk
        self.rows: dict = {}

    def cursor(self):
        @contextlib.contextmanager
        def _c():
            yield _FakeSingletonCursor(self.rows, self.pk)

        return _c()


def test_sso_config_upsert_keeps_the_fixed_id_and_round_trips_json():
    spec = TableSpec(name="LiteLLM_SSOConfig", pk="id", json_cols=frozenset({"sso_settings"}))
    repo = HanaRepository(mgr=_FakeSingletonMgr("id"), spec=spec)

    created = repo.upsert(
        where={"id": "sso_config"},
        create={"id": "sso_config", "sso_settings": {"google_client_id": "abc"}},
        update={"sso_settings": {"google_client_id": "xyz"}},
    )
    assert created["id"] == "sso_config"  # auto_uuid did NOT mint a different id

    fetched = repo.find_unique(where={"id": "sso_config"})
    assert fetched["sso_settings"] == {"google_client_id": "abc"}  # NCLOB decodes to dict, not str

    updated = repo.upsert(
        where={"id": "sso_config"},
        create={"id": "sso_config", "sso_settings": {"google_client_id": "abc"}},
        update={"sso_settings": {"google_client_id": "xyz"}},
    )
    assert updated["sso_settings"] == {"google_client_id": "xyz"}


def test_ui_settings_upsert_keeps_the_fixed_id_and_round_trips_json():
    spec = TableSpec(name="LiteLLM_UISettings", pk="id", json_cols=frozenset({"ui_settings"}))
    repo = HanaRepository(mgr=_FakeSingletonMgr("id"), spec=spec)

    created = repo.upsert(
        where={"id": "ui_settings"},
        create={"id": "ui_settings", "ui_settings": {"theme": "dark"}},
        update={"ui_settings": {"theme": "light"}},
    )
    assert created["id"] == "ui_settings"  # auto_uuid did NOT mint a different id

    fetched = repo.find_unique(where={"id": "ui_settings"})
    assert fetched["ui_settings"] == {"theme": "dark"}  # NCLOB decodes to dict, not str


def test_group_by_quotes_select_list_and_group_by_clause():
    """group_by()'s SELECT list and its GROUP BY clause must both quote
    caller-supplied group columns — a name containing a double quote must
    survive as one quoted identifier in both places."""
    mgr = _RecordingMgr(description=[("team_id",), ('a"b',)])
    repo = HanaRepository(mgr=mgr, spec=TableSpec(name="LiteLLM_Config", pk="id"))
    repo.group_by(by=["team_id", 'a"b'])
    sql, params = mgr.cur.calls[0]
    assert sql == ('SELECT "team_id", "a""b" FROM "LiteLLM_Config" GROUP BY "team_id", "a""b"')
    assert params is None
