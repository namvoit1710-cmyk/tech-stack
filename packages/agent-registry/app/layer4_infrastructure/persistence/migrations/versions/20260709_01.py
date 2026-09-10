"""add users and agent_users tables (n-n agent <-> user) - SA-1553"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.layer4_infrastructure.settings import Settings

settings = Settings()

revision: str = "20260709_01"
down_revision: Union[str, Sequence[str], None] = "20260701_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _normalized_schema() -> str | None:
    schema = settings.database_schema
    return None if not schema or schema.upper() == "PUBLIC" else schema


def _table_exists(table_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    return inspector.has_table(table_name, schema=schema)


def upgrade() -> None:
    schema = _normalized_schema()

    if not _table_exists("users", schema):
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            schema=schema,
        )
        op.create_index("ix_users_email", "users", ["email"], unique=True, schema=schema)
        op.create_index("ix_users_tenant_id", "users", ["tenant_id"], unique=False, schema=schema)

    if not _table_exists("agent_users", schema):
        op.create_table(
            "agent_users",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("agent_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            schema=schema,
        )
        op.create_index("ix_agent_user_unique", "agent_users", ["agent_id", "user_id"], unique=True, schema=schema)
        op.create_index("ix_agent_users_agent_id", "agent_users", ["agent_id"], unique=False, schema=schema)
        op.create_index("ix_agent_users_user_id", "agent_users", ["user_id"], unique=False, schema=schema)


def downgrade() -> None:
    schema = _normalized_schema()

    if _table_exists("agent_users", schema):
        op.drop_table("agent_users", schema=schema)
    if _table_exists("users", schema):
        op.drop_table("users", schema=schema)
