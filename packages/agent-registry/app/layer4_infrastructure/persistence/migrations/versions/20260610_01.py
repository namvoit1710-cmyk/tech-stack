"""add agent user_email and tenant_id columns

Revision ID: 20260610_01
Revises: 20260605_01
Create Date: 2026-06-10 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.layer4_infrastructure.settings import Settings

settings = Settings()

revision: str = "20260610_01"
down_revision: Union[str, Sequence[str], None] = "20260605_01"
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


def _index_exists(table_name: str, index_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    try:
        indexes = inspector.get_indexes(table_name, schema=schema)
    except sa.exc.NoSuchTableError:
        return False

    return any(index["name"] == index_name for index in indexes)


def upgrade() -> None:
    schema = _normalized_schema()
    if not _table_exists("agents", schema):
        return

    if not _column_exists("agents", "user_email", schema):
        op.add_column(
            "agents",
            sa.Column("user_email", sa.String(length=255), nullable=True),
            schema=schema,
        )

    if not _column_exists("agents", "tenant_id", schema):
        op.add_column(
            "agents",
            sa.Column("tenant_id", sa.String(length=36), nullable=True),
            schema=schema,
        )

    if not _index_exists("agents", "ix_agents_user_email", schema):
        op.create_index("ix_agents_user_email", "agents", ["user_email"], unique=False, schema=schema)

    if not _index_exists("agents", "ix_agents_tenant_id", schema):
        op.create_index("ix_agents_tenant_id", "agents", ["tenant_id"], unique=False, schema=schema)


def downgrade() -> None:
    schema = _normalized_schema()
    if not _table_exists("agents", schema):
        return

    if _index_exists("agents", "ix_agents_tenant_id", schema):
        op.drop_index("ix_agents_tenant_id", table_name="agents", schema=schema)

    if _index_exists("agents", "ix_agents_user_email", schema):
        op.drop_index("ix_agents_user_email", table_name="agents", schema=schema)

    if _column_exists("agents", "user_email", schema):
        op.execute(sa.text("UPDATE agents SET user_email = user_email WHERE user_email IS NULL"))
        op.drop_column("agents", "user_email", schema=schema)

    if _column_exists("agents", "tenant_id", schema):
        op.drop_column("agents", "tenant_id", schema=schema)