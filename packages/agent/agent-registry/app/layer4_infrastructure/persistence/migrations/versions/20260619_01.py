"""add custom fields for agent

Revision ID: 20260619_01
Revises: 20260701_01
Create Date: 2026-06-19 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.layer4_infrastructure.settings import Settings

settings = Settings()

revision: str = "20260619_01"
down_revision: Union[str, Sequence[str], None] = "20260701_01"
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
    return any(col["name"] == column_name for col in columns)


def upgrade() -> None:
    schema = _normalized_schema()
    if not _table_exists("agents", schema):
        return

    if not _column_exists("agents", "custom_system_prompt", schema):
        op.add_column("agents", sa.Column("custom_system_prompt", sa.Text(), nullable=True), schema=schema)

    if not _column_exists("agents", "custom_instructions", schema):
        op.add_column("agents", sa.Column("custom_instructions", sa.Text(), nullable=True), schema=schema)

    if not _column_exists("agents", "custom_restrictions", schema):
        op.add_column("agents", sa.Column("custom_restrictions", sa.Text(), nullable=True), schema=schema)

    if not _column_exists("agents", "blocked_topics", schema):
        op.add_column("agents", sa.Column("blocked_topics", sa.Text(), nullable=True), schema=schema)

    if not _column_exists("agents", "blocked_keywords", schema):
        op.add_column("agents", sa.Column("blocked_keywords", sa.Text(), nullable=True), schema=schema)


def downgrade() -> None:
    schema = _normalized_schema()
    if not _table_exists("agents", schema):
        return

    if _column_exists("agents", "custom_system_prompt", schema):
        op.drop_column("agents", "custom_system_prompt", schema=schema)

    if _column_exists("agents", "custom_instructions", schema):
        op.drop_column("agents", "custom_instructions", schema=schema)

    if _column_exists("agents", "custom_restrictions", schema):
        op.drop_column("agents", "custom_restrictions", schema=schema)

    if _column_exists("agents", "blocked_topics", schema):
        op.drop_column("agents", "blocked_topics", schema=schema)

    if _column_exists("agents", "blocked_keywords", schema):
        op.drop_column("agents", "blocked_keywords", schema=schema)
