"""add unique constraint on name and version for agents

Revision ID: 20260618_01
Revises: 20260610_01
Create Date: 2026-06-18 00:00:00
"""

import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.layer4_infrastructure.settings import Settings

logger = logging.getLogger("alembic.runtime.migration")

settings = Settings()

revision: str = "20260618_01"
down_revision: Union[str, Sequence[str], None] = "20260610_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _normalized_schema() -> str | None:
    schema = settings.database_schema
    return None if not schema or schema.upper() == "PUBLIC" else schema


def _table_exists(table_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    return inspector.has_table(table_name, schema=schema)


def _index_exists(table_name: str, index_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    try:
        indexes = inspector.get_indexes(table_name, schema=schema)
    except sa.exc.NoSuchTableError:
        return False
    return any(index["name"] == index_name for index in indexes)


def _constraint_exists(table_name: str, constraint_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    try:
        constraints = inspector.get_unique_constraints(table_name, schema=schema)
    except sa.exc.NoSuchTableError:
        return False
    return any(c["name"] == constraint_name for c in constraints)


def _dedup_duplicate_name_version(schema: str | None) -> None:
    """SA-1072/#4: resolve pre-existing duplicate (name, version) rows before adding the
    unique constraint, so the migration never aborts mid-deploy with an IntegrityError.

    Keeps the most recently updated row in each (name, version) group and renames older
    duplicates' version to a unique 'dup-<id>' value (id is the PK, so it is globally
    unique and fits String(50)). Non-destructive — no rows are deleted; a no-op when there
    are no duplicates (e.g. fresh databases).
    """
    bind = op.get_bind()
    tbl = f'"{schema}"."agents"' if schema else '"agents"'
    dup_groups = bind.execute(sa.text(
        f"SELECT COUNT(*) FROM (SELECT name, version FROM {tbl} GROUP BY name, version HAVING COUNT(*) > 1)"
    )).scalar()
    if not dup_groups:
        return
    logger.warning(
        "agents: found %s duplicate (name, version) group(s); renaming older duplicates to "
        "'dup-<id>' before creating uq_agents_name_version", dup_groups,
    )
    bind.execute(sa.text(
        f"UPDATE {tbl} SET version = 'dup-' || id WHERE id IN ("
        f" SELECT ranked.id FROM ("
        f"  SELECT id, ROW_NUMBER() OVER (PARTITION BY name, version ORDER BY updated_at DESC, id DESC) AS rn"
        f"  FROM {tbl}"
        f" ) ranked WHERE ranked.rn > 1)"
    ))


def upgrade() -> None:
    schema = _normalized_schema()
    if not _table_exists("agents", schema):
        return

    if not _constraint_exists("agents", "uq_agents_name_version", schema):
        _dedup_duplicate_name_version(schema)  # SA-1072/#4: avoid IntegrityError on pre-existing dups
        op.create_unique_constraint("uq_agents_name_version", "agents", ["name", "version"], schema=schema)

    if _index_exists("agents", "ix_agents_name", schema):
        op.drop_index("ix_agents_name", table_name="agents", schema=schema)


def downgrade() -> None:
    schema = _normalized_schema()
    if not _table_exists("agents", schema):
        return

    if not _index_exists("agents", "ix_agents_name", schema):
        op.create_index("ix_agents_name", "agents", ["name"], schema=schema)

    if _constraint_exists("agents", "uq_agents_name_version", schema):
        op.drop_constraint("uq_agents_name_version", "agents", schema=schema)
