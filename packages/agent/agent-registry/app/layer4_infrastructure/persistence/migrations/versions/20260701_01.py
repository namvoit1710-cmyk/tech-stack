"""add capability_summary column to agents

Revision ID: 20260701_01
Revises: 20260618_01
Create Date: 2026-07-01 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.layer4_infrastructure.settings import Settings

settings = Settings()

revision: str = "20260701_01"
down_revision: Union[str, Sequence[str], None] = "20260618_01"
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


def upgrade() -> None:
    schema = _normalized_schema()
    if not _table_exists("agents", schema):
        return

    if not _column_exists("agents", "capability_summary", schema):
        op.add_column(
            "agents",
            sa.Column("capability_summary", sa.Text(), nullable=True),
            schema=schema,
        )


def downgrade() -> None:
    schema = _normalized_schema()
    if not _table_exists("agents", schema):
        return

    if _column_exists("agents", "capability_summary", schema):
        op.drop_column("agents", "capability_summary", schema=schema)
