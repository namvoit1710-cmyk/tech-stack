"""Tests for litellm/proxy/db/hana/migrations.py (SA-2239).

Lives OUTSIDE the litellm package for the same collection-import reason as
test_hana_config.py / test_hana_schema.py: litellm/proxy/ has an __init__.py,
so a test placed under litellm/proxy/db/hana/ would be collected as
`litellm.proxy.db.hana.test_*` and import litellm/__init__.py, which does an
unguarded `import dotenv` and dies during collection in this lightweight env.
migrations.py is a true stdlib-only leaf (duck-typed mgr, no relative import),
so loading it by file path needs no package import at all.

Covers the SA-2239 drift-detector acceptance criteria:
  * RED             - apply_schema still skips an existing table wholesale, so
                       a column added to its DDL is never applied (current,
                       pre-existing behaviour - locked so a future change to
                       apply_schema itself cannot silently reintroduce ALTER).
  * DETECT          - verify_present() reports, per table, exactly the DDL
                       columns missing from the deployed table.
  * NO FALSE POSITIVE - a schema matching the DDL (incl. the real 19-table
                       CORE_TABLE_DDL) reports zero drift.
  * LOUD            - ensure_no_drift() is the wired path: it logs one ERROR
                       line naming the table and the missing column(s), then
                       raises, so drift can never surface as silence.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import os
import sys

import pytest

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "litellm", "proxy", "db", "hana")


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_BASE, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


migrations = _load("hana_migrations", "migrations.py")
schema = _load("hana_migrations_schema", "schema.py")


class FakeCursor:
    """Answers whichever of the two queries migrations.py issues.

    ``have`` are the table names already deployed (SYS.TABLES). ``columns``
    maps table name -> the column names actually deployed for it (SYS.TABLE_COLUMNS).
    """

    def __init__(self, have, columns=None):
        self.have = have
        self.columns = columns or {}
        self.sql = []
        self._last = ""

    def execute(self, q, *a):
        self.sql.append(q)
        self._last = q

    def fetchall(self):
        if "SYS.TABLE_COLUMNS" in self._last:
            return [(t, c) for t, cols in self.columns.items() for c in cols]
        return [(n,) for n in self.have]

    def close(self):
        pass


class FakeMgr:
    def __init__(self, have, columns=None):
        self.cur = FakeCursor(have, columns)

    def cursor(self):
        @contextlib.contextmanager
        def _c():
            yield self.cur

        return _c()


# --- RED: current apply_schema behaviour (the bug this ticket is about) -----


def test_existing_table_is_skipped_and_new_column_never_applied():
    mgr = FakeMgr({"LiteLLM_Config"})
    ddl = {"LiteLLM_Config": 'CREATE COLUMN TABLE "LiteLLM_Config" ("param_name" NVARCHAR(255), "NEW_COL" NCLOB)'}
    report = migrations.apply_schema(mgr, ddl, ["LiteLLM_Config"])
    assert report == {"created": [], "skipped": ["LiteLLM_Config"]}
    assert not any("ALTER" in q for q in mgr.cur.sql)  # no column path exists
    assert not any("CREATE" in q for q in mgr.cur.sql)  # DDL never executed


# --- DETECT: verify_present() names the missing column(s), per table -------


def test_verify_present_reports_missing_columns_per_table():
    ddl = {
        "LiteLLM_Config": 'CREATE COLUMN TABLE "LiteLLM_Config" '
        '("param_name" NVARCHAR(255) PRIMARY KEY, "param_value" NCLOB, "NEW_COL" NCLOB)'
    }
    mgr = FakeMgr(
        have={"LiteLLM_Config"},
        columns={"LiteLLM_Config": ["param_name", "param_value"]},
    )
    drift = migrations.verify_present(mgr, ddl, ["LiteLLM_Config"])
    assert drift == {"LiteLLM_Config": ["NEW_COL"]}


def test_verify_present_reports_missing_columns_for_multiple_tables():
    ddl = {
        "LiteLLM_Config": 'CREATE COLUMN TABLE "LiteLLM_Config" '
        '("param_name" NVARCHAR(255) PRIMARY KEY, "param_value" NCLOB)',
        "LiteLLM_TagTable": 'CREATE COLUMN TABLE "LiteLLM_TagTable" '
        '("tag_name" NVARCHAR(255) PRIMARY KEY, "description" NVARCHAR(5000))',
    }
    order = ["LiteLLM_Config", "LiteLLM_TagTable"]
    mgr = FakeMgr(
        have={"LiteLLM_Config", "LiteLLM_TagTable"},
        columns={
            "LiteLLM_Config": ["param_name"],  # missing param_value
            "LiteLLM_TagTable": ["tag_name", "description"],  # matches
        },
    )
    drift = migrations.verify_present(mgr, ddl, order)
    assert drift == {"LiteLLM_Config": ["param_value"]}


def test_verify_present_skips_tables_not_yet_created():
    """A table absent from SYS.TABLES is apply_schema's job, not drift."""
    ddl = {"LiteLLM_Config": 'CREATE COLUMN TABLE "LiteLLM_Config" ("param_name" NVARCHAR(255))'}
    mgr = FakeMgr(have=set(), columns={})
    assert migrations.verify_present(mgr, ddl, ["LiteLLM_Config"]) == {}


# --- NO FALSE POSITIVE: a matching schema reports zero drift ----------------


def test_verify_present_reports_no_drift_when_schema_matches():
    ddl = {
        "LiteLLM_Config": 'CREATE COLUMN TABLE "LiteLLM_Config" '
        '("param_name" NVARCHAR(255) PRIMARY KEY, "param_value" NCLOB)'
    }
    mgr = FakeMgr(
        have={"LiteLLM_Config"},
        columns={"LiteLLM_Config": ["param_name", "param_value"]},
    )
    assert migrations.verify_present(mgr, ddl, ["LiteLLM_Config"]) == {}


def test_verify_present_reports_no_drift_against_the_real_schema():
    """End-to-end sanity against the actual 25-table CORE_TABLE_DDL/TABLE_ORDER:
    a deployment whose columns exactly match the DDL must report zero drift."""
    order = schema.TABLE_ORDER
    ddl_map = schema.CORE_TABLE_DDL
    columns = {name: migrations._ddl_columns(ddl_map[name], name) for name in order}
    mgr = FakeMgr(have=set(order), columns=columns)
    assert migrations.verify_present(mgr, ddl_map, order) == {}


# --- LOUD: the wired path (ensure_no_drift) actually surfaces it ------------


def test_ensure_no_drift_raises_and_logs_error_naming_table_and_column(caplog):
    ddl = {
        "LiteLLM_Config": 'CREATE COLUMN TABLE "LiteLLM_Config" '
        '("param_name" NVARCHAR(255) PRIMARY KEY, "NEW_COL" NCLOB)'
    }
    mgr = FakeMgr(have={"LiteLLM_Config"}, columns={"LiteLLM_Config": ["param_name"]})
    with caplog.at_level(logging.ERROR, logger="laidonllm.hana.migrations"):
        with pytest.raises(RuntimeError, match="drift"):
            migrations.ensure_no_drift(mgr, ddl, ["LiteLLM_Config"])
    error_lines = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("LiteLLM_Config" in line and "NEW_COL" in line for line in error_lines)


def test_ensure_no_drift_does_not_raise_when_schema_matches(caplog):
    ddl = {"LiteLLM_Config": 'CREATE COLUMN TABLE "LiteLLM_Config" ("param_name" NVARCHAR(255))'}
    mgr = FakeMgr(have={"LiteLLM_Config"}, columns={"LiteLLM_Config": ["param_name"]})
    with caplog.at_level(logging.ERROR, logger="laidonllm.hana.migrations"):
        migrations.ensure_no_drift(mgr, ddl, ["LiteLLM_Config"])  # must not raise
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]


# --- COVERAGE (SA-2324): missing_ddl_coverage() / ensure_ddl_coverage() ------
#
# Column-level verify_present()/ensure_no_drift() (above) only reason about
# tables already deployed - a table absent from the DDL map *entirely* never
# reaches them. missing_ddl_coverage() is the sibling, coarser gate: it
# compares the full routable set (client.py's ALL_MODEL_TABLES) against
# TABLE_ORDER regardless of deployment state.


def test_missing_ddl_coverage_reports_model_not_in_table_order():
    order = ["LiteLLM_Config", "LiteLLM_TagTable"]
    all_model_tables = {
        "litellm_config": "LiteLLM_Config",
        "litellm_tagtable": "LiteLLM_TagTable",
        "litellm_agentstable": "LiteLLM_AgentsTable",  # routable, no DDL
    }
    assert migrations.missing_ddl_coverage(all_model_tables, order) == ["LiteLLM_AgentsTable"]


def test_missing_ddl_coverage_empty_when_every_routable_model_has_ddl():
    order = ["LiteLLM_Config", "LiteLLM_TagTable"]
    all_model_tables = {"litellm_config": "LiteLLM_Config", "litellm_tagtable": "LiteLLM_TagTable"}
    assert migrations.missing_ddl_coverage(all_model_tables, order) == []


# A *literal* snapshot of a representative ALL_MODEL_TABLES (client.py can't be
# imported standalone here - it pulls in hdbcli via .connection, unlike the
# stdlib-only leaves this test file loads by path; see this file's docstring).
# Deliberately NOT derived from schema.TABLE_ORDER: that's what lets removing
# an entry from the real TABLE_ORDER actually flip this test (see the mutation
# check recorded below and in .ai-task/progress.md), instead of the fixture
# silently following the mutation.
_REPRESENTATIVE_ALL_MODEL_TABLES = {
    name.lower(): name
    for name in [
        "LiteLLM_Config",
        "LiteLLM_TeamTable",
        "LiteLLM_UserTable",
        "LiteLLM_VerificationToken",
        "LiteLLM_SpendLogs",
        "LiteLLM_CredentialsTable",
        "LiteLLM_ProxyModelTable",
        "LiteLLM_BudgetTable",
        "LiteLLM_OrganizationTable",
        "LiteLLM_ObjectPermissionTable",
        "LiteLLM_EndUserTable",
        "LiteLLM_TeamMembership",
        "LiteLLM_OrganizationMembership",
        "LiteLLM_InvitationLink",
        "LiteLLM_TagTable",
        "LiteLLM_DailyUserSpend",
        "LiteLLM_DailyTeamSpend",
        "LiteLLM_DailyTagSpend",
        "LiteLLM_DailyOrganizationSpend",
        "LiteLLM_SearchToolsTable",
        # SA-2329: the three previously-silent tables now covered.
        "LiteLLM_DailyEndUserSpend",
        "LiteLLM_DeletedVerificationToken",
        "LiteLLM_DeletedTeamTable",
        # SA-2332: SSO/UI Settings save 500'd for want of DDL - now covered.
        "LiteLLM_SSOConfig",
        "LiteLLM_UISettings",
        # routable via client.py's ALL_MODEL_TABLES but never given DDL - part of
        # the ticket's measured 46-table gap (40 remaining once SA-2332 lands):
        "LiteLLM_AgentsTable",
        "LiteLLM_ModelTable",
        "LiteLLM_GuardrailsTable",
    ]
}


def test_missing_ddl_coverage_against_the_real_table_order_names_only_the_true_gap():
    """Locks the SA-2324/SA-2329/SA-2332 invariant against the REAL
    schema.TABLE_ORDER: every table either ticket added DDL for (incl.
    LiteLLM_SearchToolsTable, the three SA-2329 tables, and the SA-2332 pair)
    must NOT be reported missing, and every still-uncovered model must be.

    Mutation-checked per the SA-2310/SA-2312/SA-2324 lesson: temporarily
    removing "LiteLLM_DeletedTeamTable" from the real schema.TABLE_ORDER made
    this test fail (it started reporting LiteLLM_DeletedTeamTable as part of
    the gap, which the fixed `==` list does not expect) - confirmed by
    actually doing it and reverting.
    """
    missing = migrations.missing_ddl_coverage(_REPRESENTATIVE_ALL_MODEL_TABLES, schema.TABLE_ORDER)
    assert missing == ["LiteLLM_AgentsTable", "LiteLLM_GuardrailsTable", "LiteLLM_ModelTable"]


def test_ensure_ddl_coverage_logs_error_naming_every_missing_model(caplog):
    order = ["LiteLLM_Config"]
    all_model_tables = {"litellm_config": "LiteLLM_Config", "litellm_agentstable": "LiteLLM_AgentsTable"}
    with caplog.at_level(logging.ERROR, logger="laidonllm.hana.migrations"):
        missing = migrations.ensure_ddl_coverage(all_model_tables, order)
    assert missing == ["LiteLLM_AgentsTable"]
    error_lines = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("LiteLLM_AgentsTable" in line for line in error_lines)


def test_ensure_ddl_coverage_does_not_raise_and_logs_nothing_when_fully_covered(caplog):
    order = ["LiteLLM_Config"]
    all_model_tables = {"litellm_config": "LiteLLM_Config"}
    with caplog.at_level(logging.ERROR, logger="laidonllm.hana.migrations"):
        missing = migrations.ensure_ddl_coverage(all_model_tables, order)  # must not raise
    assert missing == []
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]


# --- MIRROR DRIFT (SA-2329): missing_mirror_columns() / ensure_mirror_coverage() ---
#
# An archival mirror (LiteLLM_DeletedVerificationToken / LiteLLM_DeletedTeamTable)
# introduces a drift axis neither verify_present() nor missing_ddl_coverage() can
# see: both reason about a table against its OWN ddl. Nothing compares the mirror
# against the live table it archives - so a column added to the source and
# forgotten on the mirror silently stops being archived.


def test_missing_mirror_columns_reports_gap_present_in_source_but_not_mirror():
    ddl = {
        "Source": 'CREATE COLUMN TABLE "Source" ("a" NVARCHAR(255) PRIMARY KEY, "b" NCLOB, "c" NCLOB)',
        "Mirror": 'CREATE COLUMN TABLE "Mirror" ("a" NVARCHAR(255) PRIMARY KEY, "b" NCLOB)',
    }
    missing = migrations.missing_mirror_columns(ddl, {"Mirror": "Source"})
    assert missing == {"Mirror": ["c"]}


def test_missing_mirror_columns_empty_when_mirror_is_a_superset():
    ddl = {
        "Source": 'CREATE COLUMN TABLE "Source" ("a" NVARCHAR(255) PRIMARY KEY, "b" NCLOB)',
        "Mirror": 'CREATE COLUMN TABLE "Mirror" ("a" NVARCHAR(255) PRIMARY KEY, "b" NCLOB, "deleted_at" TIMESTAMP)',
    }
    assert migrations.missing_mirror_columns(ddl, {"Mirror": "Source"}) == {}


def test_ensure_mirror_coverage_logs_error_naming_mirror_source_and_gap(caplog):
    ddl = {
        "Source": 'CREATE COLUMN TABLE "Source" ("a" NVARCHAR(255) PRIMARY KEY, "b" NCLOB)',
        "Mirror": 'CREATE COLUMN TABLE "Mirror" ("a" NVARCHAR(255) PRIMARY KEY)',
    }
    with caplog.at_level(logging.ERROR, logger="laidonllm.hana.migrations"):
        missing = migrations.ensure_mirror_coverage(ddl, {"Mirror": "Source"})
    assert missing == {"Mirror": ["b"]}
    error_lines = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("Mirror" in line and "Source" in line and "b" in line for line in error_lines)


def test_ensure_mirror_coverage_does_not_raise_and_logs_nothing_when_covered(caplog):
    ddl = {
        "Source": 'CREATE COLUMN TABLE "Source" ("a" NVARCHAR(255) PRIMARY KEY)',
        "Mirror": 'CREATE COLUMN TABLE "Mirror" ("a" NVARCHAR(255) PRIMARY KEY)',
    }
    with caplog.at_level(logging.ERROR, logger="laidonllm.hana.migrations"):
        missing = migrations.ensure_mirror_coverage(ddl, {"Mirror": "Source"})  # must not raise
    assert missing == {}
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]


def test_mirror_sources_against_the_real_schema_names_only_the_known_upstream_gap():
    """Locks the SA-2329 invariant against the REAL CORE_TABLE_DDL: the two
    mirrors' DDL is derived straight from schema.prisma (per the ticket), and
    the Prisma models for LiteLLM_DeletedVerificationToken /
    LiteLLM_DeletedTeamTable genuinely omit a couple of the source's columns
    (documented inline in team_endpoints.py as "not in LiteLLM_DeletedTeamTable
    schema") - a pre-existing, accepted upstream gap, not a HANA-side bug. This
    locks that gap so a NEW, unnoticed column skipped on the mirror side (as
    opposed to this known one) fails the test immediately.

    Mutation-checked: temporarily deleting the '"spend" DOUBLE DEFAULT 0,' line
    from LiteLLM_DeletedTeamTable's DDL in schema.py made this test fail (it
    reported an extra "spend" entry the fixed `==` below does not expect) -
    confirmed by actually doing it and reverting.
    """
    missing = migrations.missing_mirror_columns(schema.CORE_TABLE_DDL)
    assert missing == {
        "LiteLLM_DeletedVerificationToken": ["budget_limits"],
        "LiteLLM_DeletedTeamTable": ["budget_limits", "default_team_member_models"],
    }
