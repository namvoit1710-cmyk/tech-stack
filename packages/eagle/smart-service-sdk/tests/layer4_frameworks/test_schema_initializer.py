from pathlib import Path

import pytest

from smart_service_sdk.layer4_frameworks.hana.schema_initializer import (
    SEED_MIGRATIONS_TABLE_NAME,
    execute_sql_paths,
)


class _Cursor:
    def __init__(self, fetchone_values=None, fail_on_statement: str | None = None) -> None:
        self.commands: list[tuple[str, object]] = []
        self._fetchone_values = list(fetchone_values or [])
        self._fail_on_statement = fail_on_statement

    def execute(self, sql: str, params=None) -> None:
        self.commands.append((sql, params))
        if self._fail_on_statement and self._fail_on_statement in sql:
            raise RuntimeError("statement failed")

    def fetchone(self):
        if self._fetchone_values:
            return self._fetchone_values.pop(0)
        return None


def test_execute_sql_paths_creates_migration_table_before_processing_files(tmp_path: Path) -> None:
    sql_path = tmp_path / "001_init.sql"
    sql_path.write_text("CREATE TABLE TEST_ONE (ID INTEGER);", encoding="utf-8")
    cursor = _Cursor(fetchone_values=[None, None])

    execute_sql_paths(cursor, [sql_path], "TEST_SCHEMA", SEED_MIGRATIONS_TABLE_NAME)

    assert cursor.commands[0] == ('SET SCHEMA "TEST_SCHEMA"', None)
    assert cursor.commands[1] == (
        "SELECT 1 FROM SYS.TABLES WHERE SCHEMA_NAME = CURRENT_SCHEMA AND TABLE_NAME = ?",
        (SEED_MIGRATIONS_TABLE_NAME,),
    )
    assert (
        f'CREATE COLUMN TABLE "{SEED_MIGRATIONS_TABLE_NAME}"'
        in cursor.commands[2][0]
    )
    assert cursor.commands[3] == (
        f'SELECT 1 FROM "{SEED_MIGRATIONS_TABLE_NAME}" WHERE MIGRATION_NAME = ?',
        ("001_init.sql",),
    )


def test_execute_sql_paths_records_successful_migration(tmp_path: Path) -> None:
    sql_path = tmp_path / "001_init.sql"
    sql_path.write_text("CREATE TABLE TEST_ONE (ID INTEGER);", encoding="utf-8")
    cursor = _Cursor(fetchone_values=[(1,), None])

    execute_sql_paths(cursor, [sql_path], "TEST_SCHEMA", SEED_MIGRATIONS_TABLE_NAME)

    assert cursor.commands[-1][0] == (
        f'INSERT INTO "{SEED_MIGRATIONS_TABLE_NAME}" '
        '("MIGRATION_NAME", "CONTENT_CHECKSUM") VALUES (?, ?)'
    )
    assert cursor.commands[-1][1][0] == "001_init.sql"


def test_execute_sql_paths_skips_previously_applied_matching_migration(tmp_path: Path) -> None:
    sql_path = tmp_path / "001_init.sql"
    sql_path.write_text("CREATE TABLE TEST_ONE (ID INTEGER);", encoding="utf-8")
    matching_cursor = _Cursor(fetchone_values=[(1,), (1,)])
    execute_sql_paths(
        matching_cursor,
        [sql_path],
        "TEST_SCHEMA",
        SEED_MIGRATIONS_TABLE_NAME,
    )

    assert all(
        "CREATE TABLE TEST_ONE" not in command[0] for command in matching_cursor.commands
    )
    assert all("INSERT INTO" not in command[0] for command in matching_cursor.commands)


def test_execute_sql_paths_skips_previously_applied_migration_even_if_content_changed(tmp_path: Path) -> None:
    sql_path = tmp_path / "001_init.sql"
    sql_path.write_text("CREATE TABLE TEST_ONE (ID INTEGER);", encoding="utf-8")
    cursor = _Cursor(fetchone_values=[(1,), (1,)])

    execute_sql_paths(cursor, [sql_path], "TEST_SCHEMA", SEED_MIGRATIONS_TABLE_NAME)

    assert all("CREATE TABLE TEST_ONE" not in command[0] for command in cursor.commands)
    assert all("INSERT INTO" not in command[0] for command in cursor.commands)


def test_execute_sql_paths_does_not_record_failed_migration(tmp_path: Path) -> None:
    sql_path = tmp_path / "001_init.sql"
    sql_path.write_text("CREATE TABLE TEST_ONE (ID INTEGER);", encoding="utf-8")
    cursor = _Cursor(fetchone_values=[(1,), None], fail_on_statement="CREATE TABLE TEST_ONE")

    with pytest.raises(RuntimeError, match="statement failed"):
        execute_sql_paths(cursor, [sql_path], "TEST_SCHEMA", SEED_MIGRATIONS_TABLE_NAME)

    assert all("INSERT INTO" not in command[0] for command in cursor.commands)
