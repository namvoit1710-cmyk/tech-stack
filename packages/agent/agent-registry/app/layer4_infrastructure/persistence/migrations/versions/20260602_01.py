"""drop unique name indexes for agent registry entities

Revision ID: 20260602_01
Revises: 20260531_01
Create Date: 2026-06-02 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.layer4_infrastructure.settings import Settings

# Load settings for potential use in migrations
settings = Settings()

revision: str = "20260602_01"
down_revision: Union[str, Sequence[str], None] = "20260531_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _normalized_schema() -> str | None:
    schema = settings.database_schema
    return None if not schema or schema.upper() == "PUBLIC" else schema


def _table_exists(table_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    return inspector.has_table(table_name, schema=schema)


def _get_index(table_name: str, index_name: str, schema: str | None) -> dict | None:
    inspector = sa.inspect(op.get_bind())
    try:
        indexes = inspector.get_indexes(table_name, schema=schema)
    except sa.exc.NoSuchTableError:
        return None

    for index in indexes:
        if index["name"] == index_name:
            return index
    return None


def _qualified_table_name(table_name: str, schema: str | None) -> str:
    physical_table_name = table_name.upper()
    if not schema:
        return f'"{physical_table_name}"'
    escaped_schema = schema.replace('"', '""')
    return f'"{escaped_schema}"."{physical_table_name}"'


def _get_unique_name_constraints(table_name: str, schema: str | None) -> list[str]:
    bind = op.get_bind()
    params = {"table_name": table_name.upper()}
    schema_filter = "SCHEMA_NAME = CURRENT_SCHEMA"

    if schema:
        params["schema_name"] = schema.upper()
        schema_filter = "SCHEMA_NAME = :schema_name"

    rows = bind.execute(
        sa.text(
            f"""
            SELECT CONSTRAINT_NAME
            FROM SYS.CONSTRAINTS
            WHERE {schema_filter}
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = 'NAME'
              AND IS_PRIMARY_KEY = 'FALSE'
              AND IS_UNIQUE_KEY = 'TRUE'
            """
        ),
        params,
    ).fetchall()
    return [row[0] for row in rows]


def _drop_unique_name_constraints(
    table_name: str,
    schema: str | None,
) -> None:
    qualified_table = _qualified_table_name(table_name, schema)

    for constraint_name in _get_unique_name_constraints(table_name, schema):
        escaped_constraint = constraint_name.replace('"', '""')
        op.execute(
            sa.text(
                f'ALTER TABLE {qualified_table} DROP CONSTRAINT "{escaped_constraint}"'
            )
        )


def _ensure_unique_name_constraint(
    table_name: str,
    schema: str | None,
) -> None:
    if _get_unique_name_constraints(table_name, schema):
        return

    qualified_table = _qualified_table_name(table_name, schema)
    op.execute(sa.text(f"ALTER TABLE {qualified_table} ADD UNIQUE (name)"))


def upgrade() -> None:
    schema = _normalized_schema()

    if _table_exists("agents", schema):
        _drop_unique_name_constraints("agents", schema)

    if _table_exists("tools", schema):
        _drop_unique_name_constraints("tools", schema)

    if _table_exists("workflows", schema):
        _drop_unique_name_constraints("workflows", schema)


def downgrade() -> None:
    schema = _normalized_schema()

    if _table_exists("agents", schema):
        _ensure_unique_name_constraint("agents", schema)

    if _table_exists("tools", schema):
        _ensure_unique_name_constraint("tools", schema)

    if _table_exists("workflows", schema):
        _ensure_unique_name_constraint("workflows", schema)