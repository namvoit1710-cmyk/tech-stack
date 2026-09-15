"""Tests for the file-based HANA migration runner."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.layer4_frameworks.persistence.hana.migrations import (
    _extract_table_names,
    _LEDGER_TABLE,
    _load_migration_statements,
    _MIGRATIONS_DIR,
    migrate,
)


def _ledger_db(applied: list[str] | None = None) -> MagicMock:
    """A mock db whose migration ledger reports ``applied`` filenames.

    ``execute_query`` returns the ledger rows (lowercased ``filename`` keys,
    matching ``HanaConnectionManager.execute_query``); ``execute_write`` is a
    no-op by default. Tests that need DDL errors override ``execute_write``.
    """
    db = MagicMock()
    db.execute_query.return_value = [{"filename": f} for f in (applied or [])]
    return db


def _is_ledger_sql(call) -> bool:
    return _LEDGER_TABLE in str(call)


def _data_writes(db: MagicMock) -> list:
    """execute_write calls that are real migration DDL (not the ledger)."""
    return [c for c in db.execute_write.call_args_list if not _is_ledger_sql(c)]


# ---------------------------------------------------------------------------
# _load_migration_statements
# ---------------------------------------------------------------------------

class TestLoadStatements:

    def test_reads_files_in_order(self, tmp_path):
        (tmp_path / "002_b.sql").write_text("SELECT 2")
        (tmp_path / "001_a.sql").write_text("SELECT 1")
        (tmp_path / "003_c.sql").write_text("SELECT 3")

        with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", tmp_path):
            result = _load_migration_statements()

        assert [stmt for _, stmt in result] == ["SELECT 1", "SELECT 2", "SELECT 3"]
        assert [name for name, _ in result] == ["001_a.sql", "002_b.sql", "003_c.sql"]

    def test_splits_on_semicolons(self, tmp_path):
        (tmp_path / "001.sql").write_text("CREATE TABLE A (ID INT);\nCREATE INDEX I ON A (ID);\nALTER TABLE A ADD (X INT)")

        with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", tmp_path):
            result = _load_migration_statements()

        assert len(result) == 3
        assert result[0] == ("001.sql", "CREATE TABLE A (ID INT)")
        assert result[1] == ("001.sql", "CREATE INDEX I ON A (ID)")
        assert result[2] == ("001.sql", "ALTER TABLE A ADD (X INT)")

    def test_skips_empty_chunks(self, tmp_path):
        (tmp_path / "001.sql").write_text("SELECT 1;\n\n;\n  ;  \nSELECT 2;")

        with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", tmp_path):
            result = _load_migration_statements()

        assert len(result) == 2

    def test_missing_directory(self, tmp_path):
        missing = tmp_path / "nonexistent"

        with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", missing):
            result = _load_migration_statements()

        assert result == []


# ---------------------------------------------------------------------------
# _extract_table_names
# ---------------------------------------------------------------------------

class TestExtractTableNames:

    def test_finds_table_names(self):
        statements = [
            ("001.sql", 'CREATE COLUMN TABLE "ALPHA" ("ID" INT)'),
            ("001.sql", 'CREATE INDEX "IX" ON "ALPHA" ("ID")'),
            ("002.sql", 'CREATE COLUMN TABLE "BETA" ("ID" INT)'),
            ("003.sql", 'CREATE TABLE "GAMMA" ("ID" INT)'),
        ]
        names = _extract_table_names(statements)
        assert names == ["ALPHA", "BETA", "GAMMA"]

    def test_deduplicates(self):
        statements = [
            ("001.sql", 'CREATE COLUMN TABLE "T1" ("ID" INT)'),
            ("002.sql", 'CREATE COLUMN TABLE "T1" ("ID" INT)'),
        ]
        names = _extract_table_names(statements)
        assert names == ["T1"]


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------

class TestMigrate:

    def _setup_migrations(self, tmp_path):
        """Write 4 migration files matching the real ones (15 statements total)."""
        (tmp_path / "001.sql").write_text(
            'CREATE COLUMN TABLE "AIW_WORKFLOWS" ("ID" NVARCHAR(64));\n'
            'CREATE INDEX "IDX_AIW_WORKFLOWS_STATUS" ON "AIW_WORKFLOWS" ("STATUS")'
        )
        (tmp_path / "002.sql").write_text(
            'CREATE COLUMN TABLE "AIW_WORKFLOW_RUNS" ("ID" NVARCHAR(64));\n'
            'CREATE INDEX "IDX_AIW_RUNS_WORKFLOW" ON "AIW_WORKFLOW_RUNS" ("WORKFLOW_ID");\n'
            'CREATE INDEX "IDX_AIW_RUNS_STATUS" ON "AIW_WORKFLOW_RUNS" ("STATUS")'
        )
        (tmp_path / "003.sql").write_text(
            'CREATE COLUMN TABLE "AIW_TASKS" ("ID" NVARCHAR(128));\n'
            'CREATE INDEX "IDX_AIW_TASKS_RUN" ON "AIW_TASKS" ("RUN_ID");\n'
            'CREATE INDEX "IDX_AIW_TASKS_STATUS" ON "AIW_TASKS" ("RUN_ID", "STATUS")'
        )
        (tmp_path / "004.sql").write_text(
            'ALTER TABLE "AIW_WORKFLOWS" ADD ("METADATA" NCLOB);\n'
            'ALTER TABLE "AIW_WORKFLOWS" ADD ("UPDATED_AT" NVARCHAR(64));\n'
            'ALTER TABLE "AIW_TASKS" ADD ("OUTPUT_METADATA" NCLOB);\n'
            'ALTER TABLE "AIW_TASKS" ADD ("LOOP_CONTEXT" NCLOB);\n'
            'ALTER TABLE "AIW_TASKS" ADD ("ITERATION_INDEX" INTEGER);\n'
            'ALTER TABLE "AIW_TASKS" ADD ("IDEMPOTENCY_KEY" NVARCHAR(256));\n'
            'ALTER TABLE "AIW_TASKS" ADD ("CREATED_AT" NVARCHAR(64))'
        )
        (tmp_path / "005.sql").write_text(
            'ALTER TABLE "AIW_WORKFLOWS" ADD ("SCHEMA_VERSION" NVARCHAR(32) DEFAULT \'1.0.0\');\n'
            'ALTER TABLE "AIW_WORKFLOW_RUNS" ADD ("PARENT_RUN_ID" NVARCHAR(64));\n'
            'ALTER TABLE "AIW_WORKFLOW_RUNS" ADD ("PARENT_NODE_ID" NVARCHAR(128));\n'
            'ALTER TABLE "AIW_WORKFLOW_RUNS" ADD ("DEPTH_LEVEL" INTEGER DEFAULT 0);\n'
            'ALTER TABLE "AIW_WORKFLOW_RUNS" ADD ("TENANT_ID" NVARCHAR(256) DEFAULT \'\')'
        )
        (tmp_path / "006.sql").write_text(
            'ALTER TABLE "AIW_WORKFLOWS" ADD ("PURPOSE" NCLOB DEFAULT \'\')'
        )
        (tmp_path / "007.sql").write_text(
            'ALTER TABLE "AIW_WORKFLOWS" ADD ("ROUTING_PATH" NCLOB DEFAULT \'\')'
        )

    def _ddl_raiser(self, exc: Exception):
        """A side_effect raising ``exc`` for migration DDL but letting ledger
        writes (CREATE / UPSERT on AIW_SCHEMA_MIGRATIONS) succeed."""
        def side_effect(sql, *args, **kwargs):
            if _LEDGER_TABLE in sql:
                return None
            raise exc
        return side_effect

    def test_executes_all_statements(self, tmp_path):
        self._setup_migrations(tmp_path)
        db = _ledger_db()

        with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", tmp_path):
            migrate(db)

        # 22 real DDL statements across the 7 files (ledger writes excluded).
        assert len(_data_writes(db)) == 22

    def test_skips_duplicate_errors(self, tmp_path):
        (tmp_path / "001.sql").write_text('CREATE COLUMN TABLE "T" ("ID" INT)')

        for code in (288, 289, 308, 327):
            db = _ledger_db()
            exc = Exception("dup")
            exc.errorcode = code
            db.execute_write.side_effect = self._ddl_raiser(exc)

            with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", tmp_path):
                migrate(db)  # should not raise

    def test_raises_on_other_errors(self, tmp_path):
        (tmp_path / "001.sql").write_text('BAD SQL')
        db = _ledger_db()
        db.execute_write.side_effect = self._ddl_raiser(RuntimeError("syntax error"))

        with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", tmp_path):
            with pytest.raises(RuntimeError, match="syntax error"):
                migrate(db)

    def test_skips_cannot_shorten_field_length(self, tmp_path):
        """When migration 026 has already widened a column to NVARCHAR(1024),
        re-running migration 015 (NVARCHAR(256)) hits HANA error 7
        'cannot shorten field length'. The runner must treat this as
        'already satisfied' rather than aborting the entire migration pass.
        """
        (tmp_path / "015.sql").write_text('ALTER TABLE "T" ALTER ("ID" NVARCHAR(256))')
        db = _ledger_db()
        exc = Exception(
            "(7, 'feature not supported: cannot shorten field length: ID: "
            "line 1 col 1 (at pos 0)')"
        )
        exc.errorcode = 7
        db.execute_write.side_effect = self._ddl_raiser(exc)

        with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", tmp_path):
            migrate(db)  # should not raise

    def test_does_not_skip_other_error_7(self, tmp_path):
        """Error 7 covers many 'feature not supported' cases — only the
        specific 'cannot shorten field length' message means the schema is
        already at least as wide as requested. Other error-7 cases must
        still propagate."""
        (tmp_path / "001.sql").write_text('SOME DDL')
        db = _ledger_db()
        exc = Exception("(7, 'feature not supported: something else entirely')")
        exc.errorcode = 7
        db.execute_write.side_effect = self._ddl_raiser(exc)

        with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", tmp_path):
            with pytest.raises(Exception, match="feature not supported"):
                migrate(db)

    def test_grants_to_user(self, tmp_path):
        self._setup_migrations(tmp_path)
        db = _ledger_db()

        with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", tmp_path):
            migrate(db, grant_to_user="APP_USER")

        grant_calls = [
            c for c in db.execute_write.call_args_list if "GRANT" in str(c)
        ]
        assert len(grant_calls) == 3
        grant_stmts = [c.args[0] for c in grant_calls]
        assert any("AIW_WORKFLOWS" in s for s in grant_stmts)
        assert any("AIW_WORKFLOW_RUNS" in s for s in grant_stmts)
        assert any("AIW_TASKS" in s for s in grant_stmts)

    def test_no_grants_when_empty(self, tmp_path):
        self._setup_migrations(tmp_path)
        db = _ledger_db()

        with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", tmp_path):
            migrate(db, grant_to_user="")

        grant_calls = [
            c for c in db.execute_write.call_args_list if "GRANT" in str(c)
        ]
        assert len(grant_calls) == 0

    # ── applied-migrations ledger ──

    def test_creates_ledger_then_reads_applied(self, tmp_path):
        (tmp_path / "001.sql").write_text('CREATE COLUMN TABLE "T" ("ID" INT)')
        db = _ledger_db()

        with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", tmp_path):
            migrate(db)

        # Ledger table is created and its applied set is read once.
        assert any(
            _LEDGER_TABLE in str(c) and "CREATE" in str(c)
            for c in db.execute_write.call_args_list
        )
        assert db.execute_query.call_count == 1

    def test_skips_files_already_in_ledger(self, tmp_path):
        (tmp_path / "001.sql").write_text('CREATE COLUMN TABLE "A" ("ID" INT)')
        (tmp_path / "002.sql").write_text('CREATE COLUMN TABLE "B" ("ID" INT)')
        db = _ledger_db(applied=["001.sql"])

        with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", tmp_path):
            migrate(db)

        data = [c.args[0] for c in _data_writes(db)]
        assert not any('"A"' in s for s in data), "001 was already applied"
        assert any('"B"' in s for s in data), "002 must still run"

    def test_records_each_applied_file(self, tmp_path):
        (tmp_path / "001.sql").write_text('CREATE COLUMN TABLE "A" ("ID" INT)')
        (tmp_path / "002.sql").write_text('CREATE COLUMN TABLE "B" ("ID" INT)')
        db = _ledger_db()

        with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", tmp_path):
            migrate(db)

        upserts = [
            c for c in db.execute_write.call_args_list
            if _LEDGER_TABLE in str(c) and "UPSERT" in str(c)
        ]
        recorded = {c.args[1][0] for c in upserts}
        assert recorded == {"001.sql", "002.sql"}

    def test_unrecorded_file_is_not_marked_applied(self, tmp_path):
        """A file that raises a non-satisfied error never reaches the ledger,
        so it is retried on the next boot."""
        (tmp_path / "001.sql").write_text('BAD SQL')
        db = _ledger_db()
        db.execute_write.side_effect = self._ddl_raiser(RuntimeError("boom"))

        with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", tmp_path):
            with pytest.raises(RuntimeError):
                migrate(db)

        upserts = [
            c for c in db.execute_write.call_args_list
            if _LEDGER_TABLE in str(c) and "UPSERT" in str(c)
        ]
        assert upserts == []

    def test_ledger_read_failure_is_non_fatal(self, tmp_path):
        """An unreadable ledger degrades to 'nothing applied' — the runner
        still applies every file rather than blocking boot."""
        (tmp_path / "001.sql").write_text('CREATE COLUMN TABLE "A" ("ID" INT)')
        db = _ledger_db()
        db.execute_query.side_effect = RuntimeError("ledger unreadable")

        with patch("app.layer4_frameworks.persistence.hana.migrations._MIGRATIONS_DIR", tmp_path):
            migrate(db)  # should not raise

        assert any('"A"' in c.args[0] for c in _data_writes(db))


# ---------------------------------------------------------------------------
# Actual migration files exist
# ---------------------------------------------------------------------------

class TestMigrationFilesExist:

    def test_migration_files_exist(self):
        assert _MIGRATIONS_DIR.is_dir(), f"Directory not found: {_MIGRATIONS_DIR}"
        files = sorted(f.name for f in _MIGRATIONS_DIR.glob("*.sql"))
        assert "001_create_workflows_table.sql" in files
        assert "002_create_workflow_runs_table.sql" in files
        assert "003_create_tasks_table.sql" in files
        assert "004_add_columns.sql" in files
        assert "005_add_missing_columns.sql" in files
        assert "006_add_ai_context_fields.sql" in files
        assert "026_widen_run_and_task_id_columns.sql" in files

    def test_no_semicolons_inside_sql_comments(self):
        """The migration runner splits files on raw ';' without respecting
        comments, so a ';' inside a ``--`` comment cuts the next statement
        in half and HANA rejects it with error 257 ('invalid SQL type').
        Migration 026 first shipped with such a ';' inside its preamble and
        failed in production — this guard prevents the same bug from
        recurring in any future migration.
        """
        offenders: list[str] = []
        for sql_path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            for lineno, line in enumerate(
                sql_path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                stripped = line.lstrip()
                if stripped.startswith("--") and ";" in stripped:
                    offenders.append(f"{sql_path.name}:{lineno}: {line.rstrip()}")
        assert not offenders, (
            "Found ';' inside SQL comments — migration runner will split here:\n  "
            + "\n  ".join(offenders)
        )

    def test_migration_027_widens_idempotency_key(self):
        """Migration 027 must widen ``AIW_TASKS.IDEMPOTENCY_KEY`` past the
        old NVARCHAR(256) cap so it can hold ``{task.id}-{suffix}`` where
        task.id is now NVARCHAR(1024) (migration 026). Without this widening
        HANA rejects the insert with error 274 'inserted value too large
        for column'.
        """
        path = _MIGRATIONS_DIR / "027_widen_idempotency_key.sql"
        assert path.is_file(), f"Migration 027 not found at {path}"
        sql = path.read_text(encoding="utf-8")

        # Must widen IDEMPOTENCY_KEY to a size that fits task.id (1024) plus
        # the longest known suffix ("-edit-{ISO timestamp}" ~= 40 chars).
        assert 'ALTER TABLE "AIW_TASKS" ALTER ("IDEMPOTENCY_KEY" NVARCHAR(1280))' in sql, (
            "Migration 027 must widen AIW_TASKS.IDEMPOTENCY_KEY to NVARCHAR(1280)"
        )

    def test_migration_026_widens_id_columns_to_1024(self):
        """Migration 026 must widen run/task ID columns to NVARCHAR(1024).

        Composite child-run IDs were overflowing NVARCHAR(256) (e.g. 277-char
        IDs at 8 levels of nesting). Each ALTER must be present so re-running
        migrations recovers all affected columns.
        """
        path = _MIGRATIONS_DIR / "026_widen_run_and_task_id_columns.sql"
        assert path.is_file(), f"Migration 026 not found at {path}"
        sql = path.read_text(encoding="utf-8")

        for table, column in (
            ("AIW_TASKS", "ID"),
            ("AIW_TASKS", "RUN_ID"),
            ("AIW_WORKFLOW_RUNS", "ID"),
            ("AIW_WORKFLOW_RUNS", "PARENT_RUN_ID"),
            ("AIW_WORKFLOW_RUNS", "ROOT_RUN_ID"),
        ):
            expected = f'ALTER TABLE "{table}" ALTER ("{column}" NVARCHAR(1024))'
            assert expected in sql, (
                f"Migration 026 missing widening for {table}.{column}: "
                f"expected '{expected}'"
            )
