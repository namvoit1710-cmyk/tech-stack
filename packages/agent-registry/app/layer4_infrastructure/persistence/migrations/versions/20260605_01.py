"""replace last_heartbeat with last_health_check_at on agents

Revision ID: 20260605_01
Revises: 20260603_01
Create Date: 2026-06-05 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.layer4_infrastructure.settings import Settings

settings = Settings()

revision: str = "20260605_01"
down_revision: Union[str, Sequence[str], None] = "20260603_01"
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

    if not _column_exists("agents", "last_health_check_at", schema):
        op.add_column(
            "agents",
            sa.Column("last_health_check_at", sa.DateTime(), nullable=True),
            schema=schema,
        )

    if _column_exists("agents", "last_heartbeat", schema):
        op.drop_column("agents", "last_heartbeat", schema=schema)


def downgrade() -> None:
    schema = _normalized_schema()
    if not _table_exists("agents", schema):
        return

    if not _column_exists("agents", "last_heartbeat", schema):
        op.add_column(
            "agents",
            sa.Column("last_heartbeat", sa.DateTime(), nullable=True),
            schema=schema,
        )

    if _column_exists("agents", "last_health_check_at", schema):
        op.drop_column("agents", "last_health_check_at", schema=schema)
