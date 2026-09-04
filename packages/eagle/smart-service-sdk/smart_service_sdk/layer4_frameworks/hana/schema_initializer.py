import re
from pathlib import Path
from typing import Any

from smart_service_sdk.layer4_frameworks.hana.sql_identifiers import quote_identifier

SQL_MIGRATIONS_TABLE_NAME = "AE_SQL_MIGRATIONS"
SEED_MIGRATIONS_TABLE_NAME = "AE_SEED_MIGRATIONS"


def _graph_workspace_statement(workspace_name: str) -> str:
    entity_table = quote_identifier("AE_RAG_GRAPH_ENTITIES")
    relation_table = quote_identifier("AE_RAG_GRAPH_RELATIONS")
    return (
        f"CREATE OR REPLACE GRAPH WORKSPACE {quote_identifier(workspace_name)}\n"
        f"    VERTEX TABLE {entity_table}\n"
        '        KEY "ENTITY_ID"\n'
        f"    EDGE TABLE {relation_table}\n"
        '        KEY "RELATION_ID"\n'
        f'        SOURCE "SOURCE_ENTITY_ID" REFERENCES {entity_table}\n'
        f'        TARGET "TARGET_ENTITY_ID" REFERENCES {entity_table}'
    )


def _split_sql_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    current_lines: list[str] = []
    in_do_block = False

    for line in sql_text.splitlines():
        stripped_line = line.strip()

        if not stripped_line:
            if current_lines:
                current_lines.append(line)
            continue

        if not current_lines:
            current_lines.append(line)
            in_do_block = stripped_line.upper().startswith("DO")
            continue

        current_lines.append(line)

        if in_do_block:
            if stripped_line == "END;":
                statements.append("\n".join(current_lines).strip())
                current_lines = []
                in_do_block = False
        elif stripped_line.endswith(";"):
            statements.append("\n".join(current_lines).strip())
            current_lines = []

    if current_lines:
        statements.append("\n".join(current_lines).strip())

    return statements


_TABLE_EXISTS_BLOCK = re.compile(
    r"SELECT\s+1\s+FROM\s+SYS\.TABLES\s+WHERE\s+TABLE_NAME\s*=\s*'([^']+)'",
    re.I,
)
_INDEX_EXISTS_BLOCK = re.compile(
    r"SELECT\s+1\s+FROM\s+SYS\.INDEXES\s+WHERE\s+INDEX_NAME\s*=\s*'([^']+)'",
    re.I,
)
_TABLE_COLUMN_EXISTS_BLOCK = re.compile(
    r"SELECT\s+1\s+FROM\s+SYS\.TABLE_COLUMNS\s+WHERE\s+SCHEMA_NAME\s*=\s*CURRENT_SCHEMA\s+AND\s+TABLE_NAME\s*=\s*'([^']+)'\s+AND\s+COLUMN_NAME\s*=\s*'([^']+)'",
    re.I,
)
_EXECUTE_IMMEDIATE_BLOCK = re.compile(
    r"EXECUTE IMMEDIATE '(?P<sql>.*?)';\s*END IF;\s*END;\s*$",
    re.S,
)


def _extract_conditional_block(statement: str):
    sql_match = _EXECUTE_IMMEDIATE_BLOCK.search(statement)
    if sql_match is None:
        return None

    fallback_sql = sql_match.group("sql").lstrip("\n").rstrip().replace("''", "'")

    table_match = _TABLE_EXISTS_BLOCK.search(statement)
    if table_match is not None:
        return (
            "SELECT 1 FROM SYS.TABLES WHERE TABLE_NAME = ?",
            (table_match.group(1),),
            fallback_sql,
        )

    index_match = _INDEX_EXISTS_BLOCK.search(statement)
    if index_match is not None:
        return (
            "SELECT 1 FROM SYS.INDEXES WHERE INDEX_NAME = ?",
            (index_match.group(1),),
            fallback_sql,
        )

    column_match = _TABLE_COLUMN_EXISTS_BLOCK.search(statement)
    if column_match is not None:
        return (
            (
                "SELECT 1 FROM SYS.TABLE_COLUMNS WHERE SCHEMA_NAME = CURRENT_SCHEMA "
                "AND TABLE_NAME = ? AND COLUMN_NAME = ?"
            ),
            (column_match.group(1), column_match.group(2)),
            fallback_sql,
        )

    return None


def _is_wrapper_syntax_error(error: Exception) -> bool:
    return not isinstance(error, RuntimeError)


def _is_optional_index_or_vector_error(error: Exception, statement: str) -> bool:
    message = str(error).lower()
    statement_text = statement.lower()
    return ("syntax error" in message or "incorrect syntax near" in message) and (
        "real_vector" in statement_text
        or "create vector index" in statement_text
        or re.search(r"\bcreate index\b", statement_text) is not None
    )


def _hana_error_code(error: Exception) -> int | None:
    args = getattr(error, "args", None) or ()

    if args and isinstance(args[0], int):
        return args[0]

    if args and isinstance(args[0], tuple) and args[0]:
        first = args[0][0]
        if isinstance(first, int):
            return first

    return None


def _is_duplicate_index_error(error: Exception) -> bool:
    message = str(error).lower()
    code = _hana_error_code(error)

    return (
        code == 261
        or "column list already indexed" in message
        or "already indexed" in message
    )


def _execute_sql_statement(cursor: Any, statement: str) -> None:
    try:
        cursor.execute(statement)
        return
    except Exception as exc:
        if _is_duplicate_index_error(exc):
            return

        if not _is_wrapper_syntax_error(exc):
            raise

        fallback = _extract_conditional_block(statement)
        if fallback is None:
            raise

        exists_query, exists_params, fallback_sql = fallback
        cursor.execute(exists_query, exists_params)

        if cursor.fetchone() is not None:
            return

        try:
            cursor.execute(fallback_sql)
        except Exception as direct_error:
            if _is_duplicate_index_error(
                direct_error
            ) or _is_optional_index_or_vector_error(direct_error, fallback_sql):
                return
            raise


def _migration_table_statement(table_name: str) -> str:
    return (
        f"CREATE COLUMN TABLE {quote_identifier(table_name)} ("
        '"MIGRATION_NAME" NVARCHAR(255) NOT NULL, '
        '"CONTENT_CHECKSUM" NVARCHAR(64) NOT NULL, '
        '"APPLIED_AT" TIMESTAMP NOT NULL DEFAULT CURRENT_UTCTIMESTAMP, '
        'PRIMARY KEY ("MIGRATION_NAME"))'
    )


def _ensure_migration_table(cursor: Any, table_name: str) -> None:
    cursor.execute(
        "SELECT 1 FROM SYS.TABLES WHERE SCHEMA_NAME = CURRENT_SCHEMA AND TABLE_NAME = ?",
        (table_name,),
    )
    if cursor.fetchone() is not None:
        return

    cursor.execute(_migration_table_statement(table_name))


def _is_migration_applied(
    cursor: Any,
    migration_table_name: str,
    migration_name: str,
) -> bool:
    cursor.execute(
        (
            f"SELECT 1 FROM "
            f"{quote_identifier(migration_table_name)} WHERE MIGRATION_NAME = ?"
        ),
        (migration_name,),
    )
    return cursor.fetchone() is not None


def _record_applied_migration(
    cursor: Any,
    migration_table_name: str,
    migration_name: str,
    checksum: str,
) -> None:
    cursor.execute(
        (
            f'INSERT INTO {quote_identifier(migration_table_name)} '
            '("MIGRATION_NAME", "CONTENT_CHECKSUM") VALUES (?, ?)'
        ),
        (migration_name, checksum),
    )


def _sql_source_name(sql_source: Any) -> str:
    return str(getattr(sql_source, "name", Path(sql_source).name))


def _sql_source_text(sql_source: Any) -> str:
    if hasattr(sql_source, "read_text"):
        return sql_source.read_text(encoding="utf-8")
    return Path(sql_source).read_text(encoding="utf-8")


def _execute_sql_path(
    cursor: Any,
    sql_path: Any,
    migration_table_name: str,
) -> None:
    sql_text = _sql_source_text(sql_path)
    migration_name = _sql_source_name(sql_path)
    if _is_migration_applied(
        cursor,
        migration_table_name,
        migration_name,
    ):
        return

    for statement in _split_sql_statements(sql_text):
        _execute_sql_statement(cursor, statement)

    _record_applied_migration(
        cursor,
        migration_table_name,
        migration_name,
        "",
    )


class SchemaInitializer:
    def __init__(
        self,
        connection_factory: Any,
        sql_paths: list[Any],
        auto_create_schema: bool,
        schema_name: str = "",
        graph_workspace_name: str = "",
    ):
        self._connection_factory = connection_factory
        self._sql_paths = sorted(sql_paths, key=_sql_source_name)
        self._auto_create_schema = auto_create_schema
        self._schema_name = schema_name
        self._graph_workspace_name = graph_workspace_name.strip()

    def initialize(self) -> None:
        if not self._auto_create_schema:
            return

        if not self._schema_name:
            raise ValueError(
                "HANA_SCHEMA must be configured when AUTO_CREATE_SCHEMA is enabled"
            )

        connection = self._connection_factory.acquire_without_schema()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT 1 FROM SYS.SCHEMAS WHERE SCHEMA_NAME = ?",
                (self._schema_name,),
            )
            if cursor.fetchone() is None:
                cursor.execute(f"CREATE SCHEMA {quote_identifier(self._schema_name)}")

            execute_sql_paths(
                cursor,
                self._sql_paths,
                self._schema_name,
                SQL_MIGRATIONS_TABLE_NAME,
            )
            if self._graph_workspace_name:
                cursor.execute(_graph_workspace_statement(self._graph_workspace_name))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            self._connection_factory.release(connection)


def execute_sql_paths(
    cursor: Any,
    sql_paths: list[Any],
    schema_name: str,
    migration_table_name: str,
) -> None:
    cursor.execute(f"SET SCHEMA {quote_identifier(schema_name)}")
    _ensure_migration_table(cursor, migration_table_name)
    for sql_path in sorted(sql_paths, key=_sql_source_name):
        _execute_sql_path(cursor, sql_path, migration_table_name)
