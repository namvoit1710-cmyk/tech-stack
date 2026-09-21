"""SA-1938: agents.provider / agents.model / agents.temperature -> nullable

The registry used to substitute settings defaults (DEFAULT_PROVIDER "openai",
DEFAULT_MODEL "gpt-4", DEFAULT_TEMPERATURE 0.7) whenever the caller omitted one of
these, so an agent created without them came back carrying values nobody chose and
the caller could not tell its own choice apart from a backend-invented one.

These three are now supplied by the caller (the Agent Hub FE) and stored verbatim.
Omitted means NULL. This also matters for correctness, not just honesty: reasoning
models and several Bedrock models reject a temperature outright, and the agent-sdk
already decides per model via `supports_temperature(provider, model)`.

Upgrade:
  - ALTER `agents.provider`, `agents.model`, `agents.temperature` -> nullable.

Existing rows are untouched - they keep whatever values they were stored with, so
this is a widening change with no data migration and no behaviour change for any
agent that was created with explicit values.

Idempotent (per-column guard) + reversible downgrade (backfill NULL with the historic
defaults before restoring NOT NULL). batch_alter_table keeps the ALTERs portable
(HANA in-place / SQLite copy).

Revision ID: 20260727_01
Revises: 20260716_02
Create Date: 2026-07-27 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.layer4_infrastructure.settings import Settings

settings = Settings()

revision: str = "20260727_01"
down_revision: Union[str, Sequence[str], None] = "20260716_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (column, sqlalchemy type, value used by downgrade to satisfy NOT NULL again)
_COLUMNS = (
    ("provider", sa.String(50), "'openai'"),
    ("model", sa.String(255), "'gpt-4'"),
    ("temperature", sa.Float(), "0.7"),
)


def _normalized_schema() -> str | None:
    schema = settings.database_schema
    return None if not schema or schema.upper() == "PUBLIC" else schema


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str, schema: str | None) -> bool:
    return _inspector().has_table(table_name, schema=schema)


def _nullable_columns(schema: str | None) -> set[str]:
    try:
        cols = _inspector().get_columns("agents", schema=schema)
    except sa.exc.NoSuchTableError:
        return set()
    return {c["name"] for c in cols if c.get("nullable", False)}


def upgrade() -> None:
    schema = _normalized_schema()
    if not _table_exists("agents", schema):
        return

    already_nullable = _nullable_columns(schema)
    pending = [(name, type_) for name, type_, _ in _COLUMNS if name not in already_nullable]
    if not pending:
        return

    with op.batch_alter_table("agents", schema=schema) as batch_op:
        for name, type_ in pending:
            batch_op.alter_column(name, existing_type=type_, nullable=True)


def downgrade() -> None:
    schema = _normalized_schema()
    if not _table_exists("agents", schema):
        return

    nullable = _nullable_columns(schema)
    pending = [(name, type_, fill) for name, type_, fill in _COLUMNS if name in nullable]
    if not pending:
        return

    agents = "agents" if schema is None else f"{schema}.agents"
    for name, _, fill in pending:
        op.execute(f"UPDATE {agents} SET {name} = {fill} WHERE {name} IS NULL")

    with op.batch_alter_table("agents", schema=schema) as batch_op:
        for name, type_, _ in pending:
            batch_op.alter_column(name, existing_type=type_, nullable=False)
