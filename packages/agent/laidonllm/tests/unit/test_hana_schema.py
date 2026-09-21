"""Tests for litellm/proxy/db/hana/schema.py (SA-2237).

Lives outside the litellm package for the same collection-import reason as
test_hana_config.py (see that file's docstring) — schema.py has zero
imports so it loads trivially by file path.
"""

from __future__ import annotations

import importlib.util
import os
import sys

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "litellm", "proxy", "db", "hana")


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_BASE, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


schema = _load("hana_schema", "schema.py")
migrations = _load("hana_schema_migrations", "migrations.py")


def test_table_order_has_twenty_five_tables():
    assert len(schema.TABLE_ORDER) == 25


def test_table_order_has_no_duplicates():
    assert len(schema.TABLE_ORDER) == len(set(schema.TABLE_ORDER))


def test_core_table_ddl_has_twenty_five_entries():
    assert len(schema.CORE_TABLE_DDL) == 25


def test_table_order_and_core_table_ddl_agree():
    """Locks the invariant: a drift in either list must fail this test."""
    order_set = set(schema.TABLE_ORDER)
    ddl_set = set(schema.CORE_TABLE_DDL.keys())
    assert order_set == ddl_set
    assert order_set.symmetric_difference(ddl_set) == set()


# --- SA-2324: LiteLLM_SearchToolsTable is now created (was routable, no DDL) --


def test_search_tools_table_is_in_table_order():
    assert "LiteLLM_SearchToolsTable" in schema.TABLE_ORDER


def test_search_tools_table_ddl_declares_expected_columns():
    ddl = schema.CORE_TABLE_DDL["LiteLLM_SearchToolsTable"]
    assert '"search_tool_id" NVARCHAR(255) PRIMARY KEY' in ddl
    assert '"search_tool_name" NVARCHAR(255) UNIQUE' in ddl
    assert '"litellm_params" NCLOB' in ddl
    assert '"search_tool_info" NCLOB' in ddl


# --- SA-2329: three tables that were silently swallowing every write/read ---
# (LiteLLM_DailyEndUserSpend, LiteLLM_DeletedVerificationToken, LiteLLM_DeletedTeamTable)


def test_the_three_sa_2329_tables_are_in_table_order():
    for name in (
        "LiteLLM_DailyEndUserSpend",
        "LiteLLM_DeletedVerificationToken",
        "LiteLLM_DeletedTeamTable",
    ):
        assert name in schema.TABLE_ORDER


def test_daily_end_user_spend_reuses_the_shared_daily_spend_cols():
    """Ticket: differs from LiteLLM_DailyUserSpend by exactly one leading
    column (end_user_id instead of user_id) - both must share _DAILY_SPEND_COLS
    so the two DDLs can never drift apart column-by-column."""
    ddl = schema.CORE_TABLE_DDL["LiteLLM_DailyEndUserSpend"]
    assert ddl.endswith(schema._DAILY_SPEND_COLS)
    head = ddl[: -len(schema._DAILY_SPEND_COLS)]
    assert '"id" NVARCHAR(255) PRIMARY KEY' in head
    assert '"end_user_id" NVARCHAR(255)' in head
    assert "user_id" not in head.replace("end_user_id", "")


def test_daily_end_user_spend_ddl_declares_nineteen_columns():
    cols = migrations._ddl_columns(schema.CORE_TABLE_DDL["LiteLLM_DailyEndUserSpend"], "LiteLLM_DailyEndUserSpend")
    assert len(cols) == 19
    assert cols[:2] == ["id", "end_user_id"]


def test_deleted_verification_token_ddl_declares_forty_seven_columns():
    cols = migrations._ddl_columns(
        schema.CORE_TABLE_DDL["LiteLLM_DeletedVerificationToken"], "LiteLLM_DeletedVerificationToken"
    )
    assert len(cols) == 47
    for expected in ("id", "token", "deleted_at", "deleted_by", "litellm_changed_by"):
        assert expected in cols


def test_deleted_team_table_ddl_declares_thirty_three_columns():
    cols = migrations._ddl_columns(schema.CORE_TABLE_DDL["LiteLLM_DeletedTeamTable"], "LiteLLM_DeletedTeamTable")
    assert len(cols) == 33
    for expected in ("id", "team_id", "deleted_at", "deleted_by", "litellm_changed_by"):
        assert expected in cols


# --- SA-2332: LiteLLM_SSOConfig / LiteLLM_UISettings are now created (were ---
# routable, no DDL - Save on the Admin UI's SSO/UI Settings pages 500'd)


def test_sso_config_and_ui_settings_are_in_table_order():
    assert "LiteLLM_SSOConfig" in schema.TABLE_ORDER
    assert "LiteLLM_UISettings" in schema.TABLE_ORDER


def test_sso_config_ddl_declares_expected_columns():
    ddl = schema.CORE_TABLE_DDL["LiteLLM_SSOConfig"]
    assert '"id" NVARCHAR(255) PRIMARY KEY' in ddl
    assert '"sso_settings" NCLOB' in ddl
    assert '"created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP' in ddl
    assert '"updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP' in ddl


def test_ui_settings_ddl_declares_expected_columns():
    ddl = schema.CORE_TABLE_DDL["LiteLLM_UISettings"]
    assert '"id" NVARCHAR(255) PRIMARY KEY' in ddl
    assert '"ui_settings" NCLOB' in ddl
    assert '"created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP' in ddl
    assert '"updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP' in ddl
