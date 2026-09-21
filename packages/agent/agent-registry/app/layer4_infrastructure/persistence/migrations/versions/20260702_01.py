"""SA-1072: add user_roles column

The agents table gained a user_roles column in the model/repository for the SA-1072
role-based filtering feature, but no migration added it — on Alembic-managed databases
the column would be missing and the first INSERT/SELECT would fail. This migration adds
it. Stacks on 20260619_01 (the current single head after the #1493 head-linearization).

Revision ID: 20260702_01
Revises: 20260619_01
Create Date: 2026-07-02 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.layer4_infrastructure.settings import Settings

settings = Settings()

revision: str = "20260702_01"
down_revision: Union[str, Sequence[str], None] = "20260619_01"
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

    if not _column_exists("agents", "user_roles", schema):
        op.add_column("agents", sa.Column("user_roles", sa.Text(), nullable=True), schema=schema)


def downgrade() -> None:
    schema = _normalized_schema()
    if not _table_exists("agents", schema):
        return

    if _column_exists("agents", "user_roles", schema):
        op.drop_column("agents", "user_roles", schema=schema)
