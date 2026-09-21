"""add workflow metadata and schema columns

Revision ID: 20260603_01
Revises: 20260602_01
Create Date: 2026-06-03 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.layer4_infrastructure.settings import Settings

# Load settings for potential use in migrations
settings = Settings()


revision: str = "20260603_01"
down_revision: Union[str, Sequence[str], None] = "20260602_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _normalized_schema() -> str | None:
    schema = settings.database_schema
    return None if not schema or schema.upper() == "PUBLIC" else schema


def _table_exists(table_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    return inspector.has_table(table_name, schema=schema)


def _column_exists(table_name: str, column_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    try:
        columns = inspector.get_columns(table_name, schema=schema)
    except sa.exc.NoSuchTableError:
        return False

    return any(column["name"] == column_name for column in columns)


def _add_column_if_missing(table_name: str, column: sa.Column, schema: str | None) -> None:
    if _column_exists(table_name, column.name, schema):
        return
    op.add_column(table_name, column, schema=schema)


def upgrade() -> None:
    schema = _normalized_schema()
    if not _table_exists("workflows", schema):
        return

    _add_column_if_missing("workflows", sa.Column("metadata", sa.Text(), nullable=True), schema)
    _add_column_if_missing("workflows", sa.Column("input_schema", sa.Text(), nullable=True), schema)
    _add_column_if_missing("workflows", sa.Column("output_schema", sa.Text(), nullable=True), schema)
    _add_column_if_missing("workflows", sa.Column("main_flow", sa.Boolean(), nullable=False, server_default=sa.false()), schema)


def downgrade() -> None:
    schema = _normalized_schema()
    if not _table_exists("workflows", schema):
        return

    if _column_exists("workflows", "output_schema", schema):
        op.drop_column("workflows", "output_schema", schema=schema)
    if _column_exists("workflows", "input_schema", schema):
        op.drop_column("workflows", "input_schema", schema=schema)
    if _column_exists("workflows", "metadata", schema):
        op.drop_column("workflows", "metadata", schema=schema)
    if _column_exists("workflows", "main_flow", schema):
        op.drop_column("workflows", "main_flow", schema=schema)