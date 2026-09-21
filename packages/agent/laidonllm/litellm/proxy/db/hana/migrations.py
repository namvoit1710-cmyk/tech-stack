"""Idempotent HANA schema migrations for LaidonLLM (SA-1488, AC3; drift detection SA-2239).

HANA has no reliable ``CREATE TABLE IF NOT EXISTS`` across versions, so we
consult ``SYS.TABLES`` for the current schema and create only what's missing.
Connection is autocommit, so each DDL commits immediately.

``apply_schema`` only ever checks table *existence* — an existing table is
skipped entirely, so a column added to a table's DDL after the table was first
deployed never materialises there (SA-2239). ``verify_present`` closes that gap
by also comparing each already-deployed table's columns (from
``SYS.TABLE_COLUMNS``) against its DDL and reporting any that are missing.
Detection only: no ALTER is emitted and no data is touched.

Leaf module: takes a duck-typed connection manager (see ``connection`` module)
and a DDL map, so it carries no package imports and stays standalone-testable.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger("laidonllm.hana.migrations")

_QUOTED_IDENTIFIER = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"')


def existing_tables(mgr: Any) -> set:
    """Table names present in the connection's current schema."""
    with mgr.cursor() as cur:
        cur.execute("SELECT TABLE_NAME FROM SYS.TABLES WHERE SCHEMA_NAME = CURRENT_SCHEMA")
        return {row[0] for row in cur.fetchall()}


def apply_schema(mgr: Any, ddl_map: Dict[str, str], order: List[str]) -> Dict[str, List[str]]:
    """Create any missing tables from ``ddl_map`` in ``order``. Returns a report."""
    have = existing_tables(mgr)
    created: List[str] = []
    skipped: List[str] = []
    for name in order:
        if name in have:
            skipped.append(name)
            continue
        ddl = ddl_map[name]
        with mgr.cursor() as cur:
            cur.execute(ddl)
        created.append(name)
        logger.info("HANA migration: created table %s", name)
    return {"created": created, "skipped": skipped}


def _ddl_columns(ddl: str, table_name: str) -> List[str]:
    """Column names declared by a ``CREATE TABLE`` DDL string, in order.

    Every identifier in this DDL dialect is double-quoted (see ``schema.py``),
    so one pass over quoted identifiers finds every column; the table name is
    the only other quoted identifier and is excluded by exact match (a
    composite ``PRIMARY KEY (...)`` clause may repeat column names already
    seen, so duplicates are dropped while preserving first-seen order).
    """
    seen: Dict[str, None] = {}
    for identifier in _QUOTED_IDENTIFIER.findall(ddl):
        if identifier != table_name:
            seen.setdefault(identifier, None)
    return list(seen)


def verify_present(mgr: Any, ddl_map: Dict[str, str], order: List[str]) -> Dict[str, List[str]]:
    """Verify each already-deployed table in ``order`` truly matches its DDL.

    Returns, per table, the DDL-declared columns missing from the deployed
    table (a table with no drift is absent from the result, so an empty dict
    means no drift anywhere). A table that does not exist yet is not drift —
    ``apply_schema`` will create it fresh with every DDL column — so only
    tables already present in ``SYS.TABLES`` are checked.

    Read-only: issues a single ``SYS.TABLE_COLUMNS`` query, emits no ALTER or
    other DDL, and touches no data.
    """
    have = existing_tables(mgr)
    present = [name for name in order if name in have]
    if not present:
        return {}
    with mgr.cursor() as cur:
        cur.execute(
            "SELECT TABLE_NAME, COLUMN_NAME FROM SYS.TABLE_COLUMNS WHERE SCHEMA_NAME = CURRENT_SCHEMA"
        )
        actual: Dict[str, set] = {}
        for table_name, column_name in cur.fetchall():
            actual.setdefault(table_name, set()).add(column_name)
    drift: Dict[str, List[str]] = {}
    for name in present:
        have_cols = actual.get(name, set())
        missing = [c for c in _ddl_columns(ddl_map[name], name) if c not in have_cols]
        if missing:
            drift[name] = missing
    return drift


def ensure_no_drift(mgr: Any, ddl_map: Dict[str, str], order: List[str]) -> None:
    """Fail loudly if any deployed table is missing a column its DDL declares.

    Logs one ERROR line per drifted table — naming the table and its missing
    column(s) — before raising, so the drift is visible even if a caller wraps
    this call in a tolerant ``try/except`` (see ``client.py:_ensure_schema``).
    """
    drift = verify_present(mgr, ddl_map, order)
    if not drift:
        return
    for table, missing in drift.items():
        logger.error(
            "HANA schema drift: table %s is missing column(s) %s — declared in "
            "its DDL but never applied to the deployed table",
            table,
            ", ".join(missing),
        )
    raise RuntimeError(f"HANA schema drift detected: {drift}")


def missing_ddl_coverage(all_model_tables: Dict[str, str], order: List[str]) -> List[str]:
    """Models ``client.py`` can route (``ALL_MODEL_TABLES``) that have no DDL in
    ``order`` (``TABLE_ORDER``) at all (SA-2324).

    This is coarser than — and cannot be folded into — ``verify_present``:
    that check is column-level and only reasons about tables already deployed,
    so a table missing from the DDL map entirely (not merely undeployed) is
    invisible to it. Read-only: no query, no side effect.
    """
    order_set = set(order)
    return sorted(name for name in set(all_model_tables.values()) if name not in order_set)


def ensure_ddl_coverage(all_model_tables: Dict[str, str], order: List[str]) -> List[str]:
    """Log one ERROR line naming every routable model that has no DDL (SA-2324).

    Detection only, like ``ensure_no_drift``, but does NOT raise: unlike a
    drift, this gap is a known, pre-existing condition (most of the 46 tables
    are an accepted product decision, not a regression to abort boot over) —
    it should be loud in the logs so it's never silently discovered by a user
    500 again. Returns the missing list so callers/tests can assert on it.
    """
    missing = missing_ddl_coverage(all_model_tables, order)
    if missing:
        logger.error(
            "HANA schema coverage: %d model(s) are routable (ALL_MODEL_TABLES) but "
            "have no DDL in TABLE_ORDER, so any operation against them fails with "
            "HANA error 259 (invalid table name): %s",
            len(missing),
            ", ".join(missing),
        )
    return missing


# Mirror table -> the live table it archives (SA-2329). A mirror's DDL must stay
# a superset of its source's: unlike ensure_no_drift/ensure_ddl_coverage, which
# guard a table against its OWN DDL, nothing else compares an archival copy
# against the table it was cloned from.
MIRROR_SOURCES: Dict[str, str] = {
    "LiteLLM_DeletedVerificationToken": "LiteLLM_VerificationToken",
    "LiteLLM_DeletedTeamTable": "LiteLLM_TeamTable",
}


def missing_mirror_columns(
    ddl_map: Dict[str, str], mirror_sources: Dict[str, str] = MIRROR_SOURCES
) -> Dict[str, List[str]]:
    """Per mirror table, the source's DDL columns absent from the mirror's own
    DDL (SA-2329). Compares the DDL definitions directly (not a live deployment),
    so a column added to the source's DDL without the mirror following is caught
    the moment schema.py changes, not only once ``ensure_no_drift`` eventually
    notices the *deployed* mirror table has drifted from its *own* DDL. Extra
    columns on the mirror (e.g. ``deleted_at``) are fine - only a gap relative
    to the source is reported. Read-only: no query, no side effect.
    """
    missing: Dict[str, List[str]] = {}
    for mirror, source in mirror_sources.items():
        if mirror not in ddl_map or source not in ddl_map:
            continue
        mirror_cols = set(_ddl_columns(ddl_map[mirror], mirror))
        source_cols = set(_ddl_columns(ddl_map[source], source))
        gap = sorted(source_cols - mirror_cols)
        if gap:
            missing[mirror] = gap
    return missing


def ensure_mirror_coverage(
    ddl_map: Dict[str, str], mirror_sources: Dict[str, str] = MIRROR_SOURCES
) -> Dict[str, List[str]]:
    """Log one ERROR line per mirror table whose DDL has fallen behind its
    source's (SA-2329). Detection only, like ``ensure_ddl_coverage`` - does NOT
    raise, so a lagging mirror is a loud startup log line rather than a boot
    failure. Returns the missing map so callers/tests can assert on it.
    """
    missing = missing_mirror_columns(ddl_map, mirror_sources)
    for mirror, cols in missing.items():
        logger.error(
            "HANA schema mirror drift: %s is missing column(s) %s present in its "
            "source %s - the mirror has silently stopped archiving those columns",
            mirror,
            ", ".join(cols),
            mirror_sources[mirror],
        )
    return missing
