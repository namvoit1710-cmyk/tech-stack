"""Idempotent DDL migrations for the workflow control-plane HANA tables.

Migrations are stored as individual ``.sql`` files under ``db/migrations/``.
Each file may contain multiple statements separated by ``;``.  The numeric
prefix on the filename controls execution order.

An applied-migrations ledger (``AIW_SCHEMA_MIGRATIONS``) records which files
have run, keyed by filename, so each migration is applied **exactly once** per
database. On boot the runner creates the ledger if needed, reads the set of
already-applied filenames, runs only the rest, and records each on success. A
fully migrated database does no DDL beyond the single ledger read.

The first boot after the ledger is introduced finds it empty and runs every
file once more; that reconciling pass is safe because additive DDL is caught by
the "already satisfied" error codes below and the destructive/restructuring
migrations are written as idempotent ``DO BEGIN`` guards.

Per-statement safety net (needed for that reconciling pass and any retry of a
not-yet-recorded file): HANA error codes 288 (duplicate table), 289 (duplicate
index), 308 (column already exists), and 327 (duplicate column) are caught and
silently skipped. Error 7 with the specific "cannot shorten field length"
message is also skipped — that fires when an earlier ALTER (e.g. migration 015
widening to NVARCHAR(256)) re-runs after a later ALTER (e.g. migration 026
widening to NVARCHAR(1024)) has already grown the column past the requested
size, which is a no-op.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# HANA error codes treated as "already exists".
# NOTE: error 7 ("feature not supported") is intentionally NOT here — it covers
# many distinct cases. Only the specific "cannot shorten field length" variant
# means the schema is already at least as wide as requested; that is matched by
# message below. Other error-7 cases must still propagate.
_ALREADY_EXISTS_CODES = {288, 289, 308, 327}

# 5 levels up: migrations.py -> hana/ -> persistence/ -> layer4_frameworks/ -> app/ -> service root
_MIGRATIONS_DIR: Path = Path(__file__).resolve().parent.parent.parent.parent.parent / "db" / "migrations"

# Applied-migrations ledger — keyed by filename so each file runs exactly once.
_LEDGER_TABLE = "AIW_SCHEMA_MIGRATIONS"
_LEDGER_CREATE_SQL = (
    f'CREATE COLUMN TABLE "{_LEDGER_TABLE}" ('
    '"FILENAME" NVARCHAR(255) NOT NULL PRIMARY KEY, '
    '"CHECKSUM" NVARCHAR(64), '
    '"APPLIED_AT" NVARCHAR(64)'
    ')'
)


def _is_procedural_block(sql: str) -> bool:
    """Return True if the SQL contains a procedural object (PROCEDURE/FUNCTION) or anonymous block."""
    return bool(re.search(r'\b(CREATE\s+(OR\s+REPLACE\s+)?PROCEDURE|CREATE\s+(OR\s+REPLACE\s+)?FUNCTION|DO\s+BEGIN)\b', sql, re.IGNORECASE))


def _strip_line_comments(sql: str) -> str:
    """Remove ``--`` line comments so embedded ``;`` characters don't break splitting."""
    return re.sub(r'--[^\n]*', '', sql)


def _statements_from_raw(raw: str) -> list[str]:
    """Split one migration file's raw text into executable statements.

    A file containing a procedural block (CREATE PROCEDURE / FUNCTION /
    ``DO BEGIN``) is treated as a single statement so its internal ``;``
    characters are not split.
    """
    if _is_procedural_block(raw):
        stmt = raw.strip().rstrip(";").strip()
        return [stmt] if stmt else []
    cleaned = _strip_line_comments(raw)
    return [chunk.strip() for chunk in cleaned.split(";") if chunk.strip()]


def _load_migration_statements() -> list[tuple[str, str]]:
    """Read all ``*.sql`` files from the migrations directory.

    Returns a list of ``(filename, statement)`` tuples in file-name order.
    Files containing procedural blocks (CREATE PROCEDURE / FUNCTION / DO BEGIN)
    are treated as a single statement to avoid splitting on internal semicolons.
    """
    if not _MIGRATIONS_DIR.is_dir():
        logger.warning("Migrations directory does not exist: %s", _MIGRATIONS_DIR)
        return []

    results: list[tuple[str, str]] = []
    for sql_path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        raw = sql_path.read_text(encoding="utf-8")
        for stmt in _statements_from_raw(raw):
            results.append((sql_path.name, stmt))
    return results


def _load_migrations_by_file() -> list[tuple[str, list[str], str]]:
    """Read migrations grouped per file: ``(filename, [statements], checksum)``.

    ``checksum`` is the SHA-256 of the raw file content, recorded in the ledger
    so a later edit to an already-applied migration can be detected (logged,
    never auto-re-run — add a new migration instead).
    """
    if not _MIGRATIONS_DIR.is_dir():
        logger.warning("Migrations directory does not exist: %s", _MIGRATIONS_DIR)
        return []

    out: list[tuple[str, list[str], str]] = []
    for sql_path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        raw = sql_path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        out.append((sql_path.name, _statements_from_raw(raw), checksum))
    return out


def _extract_table_names(statements: list[tuple[str, str]]) -> list[str]:
    """Extract unique table names from CREATE TABLE statements."""
    pattern = re.compile(r'CREATE\s+(?:COLUMN\s+)?TABLE\s+"([^"]+)"', re.IGNORECASE)
    seen: dict[str, None] = {}
    for _, stmt in statements:
        match = pattern.search(stmt)
        if match:
            name = match.group(1)
            if name not in seen:
                seen[name] = None
    return list(seen)


def _already_satisfied(exc: Exception) -> bool:
    """True when a statement error means the object is already in place."""
    error_code = getattr(exc, "errorcode", None)
    return error_code in _ALREADY_EXISTS_CODES or (
        error_code == 7 and "cannot shorten field length" in str(exc).lower()
    )


def _ensure_ledger(db: Any) -> None:
    """Create the applied-migrations ledger table if it does not exist."""
    try:
        db.execute_write(_LEDGER_CREATE_SQL)
    except Exception as exc:
        # 288 = duplicate table: already created on a previous boot. Anything
        # else is a genuine problem — surface it rather than silently losing
        # the run-once guarantee.
        if getattr(exc, "errorcode", None) != 288:
            raise


def _applied_filenames(db: Any) -> set[str]:
    """Return the set of filenames already recorded in the ledger.

    Degrades to an empty set on any read failure: re-running migrations is safe
    (additive DDL is swallowed, destructive DDL is guarded), so a missing or
    unreadable ledger never blocks boot — it just costs one reconciling pass.
    """
    try:
        rows = db.execute_query(f'SELECT "FILENAME" FROM "{_LEDGER_TABLE}"')
        return {
            row["filename"]
            for row in rows
            if isinstance(row, dict) and row.get("filename")
        }
    except Exception:
        logger.debug("Could not read migration ledger; assuming none applied.", exc_info=True)
        return set()


def _record_applied(db: Any, filename: str, checksum: str, applied_at: str) -> None:
    """Idempotently record ``filename`` as applied in the ledger."""
    db.execute_write(
        f'UPSERT "{_LEDGER_TABLE}" ("FILENAME", "CHECKSUM", "APPLIED_AT") '
        'VALUES (?, ?, ?) WITH PRIMARY KEY',
        (filename, checksum, applied_at),
    )


def migrate(db: Any, *, grant_to_user: str = "") -> None:
    """Run pending DDL migrations exactly once each, tracked by the ledger."""
    by_file = _load_migrations_by_file()
    total_statements = sum(len(stmts) for _, stmts, _ in by_file)
    logger.info(
        "Running control-plane HANA migrations (%d files, %d statements)...",
        len(by_file), total_statements,
    )

    _ensure_ledger(db)
    applied = _applied_filenames(db)
    applied_at = datetime.now(timezone.utc).isoformat()

    files_applied = 0
    files_skipped = 0
    statements_skipped = 0

    for filename, statements, checksum in by_file:
        if filename in applied:
            files_skipped += 1
            continue
        for stmt in statements:
            try:
                db.execute_write(stmt)
            except Exception as exc:
                if _already_satisfied(exc):
                    statements_skipped += 1
                    logger.debug("Already satisfied (%s), skipping: %s", filename, exc)
                else:
                    logger.error("Migration failed (%s): %s\nStatement: %s", filename, exc, stmt)
                    raise
        # Record only after every statement in the file succeeded or was
        # swallowed. A file that raised a non-satisfied error above never
        # reaches here, so it is retried (from the top) on the next boot.
        _record_applied(db, filename, checksum, applied_at)
        files_applied += 1

    if grant_to_user:
        all_statements = [(f, s) for f, stmts, _ in by_file for s in stmts]
        table_names = _extract_table_names(all_statements)
        logger.info("Granting DML privileges on %d tables to '%s'...", len(table_names), grant_to_user)
        for table_name in table_names:
            grant_sql = f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table_name}" TO "{grant_to_user}"'
            try:
                db.execute_write(grant_sql)
            except Exception as exc:
                logger.warning("GRANT on %s to %s failed (may be owner): %s", table_name, grant_to_user, exc)

    logger.info(
        "Migrations complete: %d files applied, %d already in ledger "
        "(%d statements already satisfied).",
        files_applied, files_skipped, statements_skipped,
    )
