"""SA-2050 (R3) — migration 051 asserts the mirror table's load-bearing shape.

Checks the table exists, PK is ID, and WORKER_TYPE is indexed (the observability
query ``... WHERE WORKER_TYPE=...`` that lists the N replicas). Deliberately does
NOT assert every optional column so it stays robust to the Task 0 host/pid/
started_at decision.
"""
from __future__ import annotations

from app.layer4_frameworks.persistence.hana.migrations import _MIGRATIONS_DIR


def _sql() -> str:
    path = _MIGRATIONS_DIR / "051_create_aiw_worker_registry_mirror.sql"
    return path.read_text(encoding="utf-8").upper()


def test_migration_051_creates_mirror_table():
    assert 'CREATE COLUMN TABLE "AIW_WORKER_REGISTRY_MIRROR"' in _sql()


def test_migration_051_declares_id_primary_key():
    assert 'PRIMARY KEY ("ID")' in _sql()


def test_migration_051_indexes_worker_type():
    sql = _sql()
    assert "CREATE INDEX" in sql
    assert 'ON "AIW_WORKER_REGISTRY_MIRROR" ("WORKER_TYPE")' in sql
