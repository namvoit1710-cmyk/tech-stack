"""RBAC v1 contract migration: drop agent_users, users.email -> nullable (T5)

The B2 CONTRACT phase, shipped in the SAME release as the backend that stops touching
`agent_users` (T5 removed the assign_user/soft_delete-agent_users code, the /mine /assign
/assignable endpoints, and the register user-context block).

Upgrade:
  - DROP TABLE `agent_users` (the legacy direct-assignment junction).
  - ALTER `users.email` -> nullable: the RBAC mirror keys on `external_id`; email is
    cosmetic, so a PM-without-email user stores NULL (not the "" placeholder — closes B3).

KEPT (deferred): `agents.user_email` / `agents.tenant_id` columns (removal deferred; the
app just stops populating them).

Idempotent (guards) + reversible downgrade (recreate agent_users, email NOT NULL after a
''-backfill). batch_alter_table keeps the email ALTER portable (HANA in-place / SQLite copy).

Revision ID: 20260716_02
Revises: 20260716_01
Create Date: 2026-07-19 00:00:00
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.layer4_infrastructure.settings import Settings

settings = Settings()

revision: str = "20260716_02"
down_revision: Union[str, Sequence[str], None] = "20260716_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _normalized_schema() -> str | None:
    schema = settings.database_schema
    return None if not schema or schema.upper() == "PUBLIC" else schema


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str, schema: str | None) -> bool:
    return _inspector().has_table(table_name, schema=schema)


def _email_is_nullable(schema: str | None) -> bool:
    try:
        cols = _inspector().get_columns("users", schema=schema)
    except sa.exc.NoSuchTableError:
        return True
    return any(c["name"] == "email" and c.get("nullable", False) for c in cols)


def upgrade() -> None:
    schema = _normalized_schema()

    # 1. Drop the legacy direct-assignment table (no backend touches it after T5).
    if _table_exists("agent_users", schema):
        op.drop_table("agent_users", schema=schema)

    # 2. users.email -> nullable (external_id is the key; email is cosmetic).
    if _table_exists("users", schema) and not _email_is_nullable(schema):
        with op.batch_alter_table("users", schema=schema) as batch_op:
            batch_op.alter_column("email", existing_type=sa.String(255), nullable=True)


def downgrade() -> None:
    schema = _normalized_schema()

    # 1. users.email -> NOT NULL (backfill any NULL to '' first so the constraint holds).
    if _table_exists("users", schema) and _email_is_nullable(schema):
        users = "users" if schema is None else f"{schema}.users"
        op.execute(f"UPDATE {users} SET email = '' WHERE email IS NULL")
        with op.batch_alter_table("users", schema=schema) as batch_op:
            batch_op.alter_column("email", existing_type=sa.String(255), nullable=False)

    # 2. Recreate the legacy agent_users junction (structure restored; FK backstop omitted).
    if not _table_exists("agent_users", schema):
        op.create_table(
            "agent_users",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("agent_id", sa.String(36), nullable=False),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False,
                      default=lambda: datetime.now(timezone.utc)),
            sa.UniqueConstraint("agent_id", "user_id", name="uq_agent_users_agent_user"),
            schema=schema,
        )
