"""SAP HANA implementation of IEnvSectionSchemaRepo (table AIW_ENV_SECTION_SCHEMA,
SA-2471).

Mirrors ``HanaEnvironmentRepo``: raw parameterized SQL through the shared ``db``
handle, CAS on the VERSION column, soft-delete via IS_ACTIVE = 0. The natural key
(TENANT_ID, SECTION) is enforced here for ACTIVE rows only, so a soft-deleted
section schema can be recreated — matching ``MemoryEnvSectionSchemaRepo``; the
migration therefore uses a non-unique index.

KEYS is stored as a JSON array of ``{key, type}``. No secrets are persisted — a
schema owns only key names and their types.
"""
from __future__ import annotations

import json
from typing import Any

from app.layer1_domain.entities.env_section_schema import EnvSectionSchema
from app.layer2_application.repositories.env_section_schema_repo_interface import (
    ListEnvSectionSchemasFilter,
    ListEnvSectionSchemasPage,
)
from app.layer2_application.repositories.gateway_app_repo_interface import CASMismatch
from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin


class HanaEnvSectionSchemaRepo(AsyncRepoMixin):
    def __init__(self, db: Any) -> None:
        self._db = db
        self._table = "AIW_ENV_SECTION_SCHEMA"

    _DATA_COLUMNS = (
        "ID", "TENANT_ID", "SECTION", "KEYS", "IS_ACTIVE",
        "CREATED_BY", "CREATED_AT", "UPDATED_BY", "UPDATED_AT",
    )

    @staticmethod
    def _dump_keys(entity: EnvSectionSchema) -> str:
        return json.dumps([
            {"key": k.key, "type": getattr(k.type, "value", k.type)}
            for k in entity.keys
        ])

    def _data_values(self, entity: EnvSectionSchema) -> tuple:
        return (
            entity.id,
            entity.tenant_id,
            entity.section or "",
            self._dump_keys(entity),
            1 if entity.is_active else 0,
            entity.created_by or "",
            entity.created_at or "",
            entity.updated_by or "",
            entity.updated_at or "",
        )

    def _fetch_version(self, id: str) -> int | None:
        rows = self._db.execute_query(
            f"SELECT VERSION FROM {self._table} WHERE ID = :p0", (id,),
        )
        return int(rows[0]["version"]) if rows else None

    def _assert_natural_key_free(self, entity: EnvSectionSchema) -> None:
        """Reject a second ACTIVE row for the same (tenant, section)."""
        rows = self._db.execute_query(
            f"SELECT ID FROM {self._table} "
            f"WHERE TENANT_ID = :p0 AND SECTION = :p1 "
            f"AND IS_ACTIVE = 1 AND ID <> :p2",
            (entity.tenant_id, entity.section or "", entity.id),
        )
        if rows:
            raise CASMismatch(
                f"section schema ({entity.tenant_id}/{entity.section}) already exists"
            )

    def save(self, entity: EnvSectionSchema, *, expected_version: int | None = None) -> None:
        existing_version = self._fetch_version(entity.id)

        if existing_version is None:
            if expected_version is not None:
                raise CASMismatch(
                    f"expected_version={expected_version} but no row exists for id={entity.id}"
                )
            if entity.is_active:
                self._assert_natural_key_free(entity)
            cols = ", ".join(self._DATA_COLUMNS) + ", VERSION"
            placeholders = ", ".join(f":p{i}" for i in range(len(self._DATA_COLUMNS) + 1))
            sql = f"INSERT INTO {self._table} ({cols}) VALUES ({placeholders})"
            self._db.execute_write(sql, self._data_values(entity) + (entity.version,))
            return

        if expected_version is not None and existing_version != expected_version:
            raise CASMismatch(
                f"expected_version={expected_version} but stored version={existing_version}"
            )
        if entity.is_active:
            self._assert_natural_key_free(entity)

        set_cols = [c for c in self._DATA_COLUMNS if c != "ID"]
        set_clause = ", ".join(f"{c} = :p{i}" for i, c in enumerate(set_cols))
        set_clause += f", VERSION = :p{len(set_cols)}"
        where_id = f":p{len(set_cols) + 1}"
        sql = f"UPDATE {self._table} SET {set_clause} WHERE ID = {where_id}"
        params = self._data_values(entity)[1:] + (entity.version, entity.id)
        if expected_version is not None:
            sql += f" AND VERSION = :p{len(set_cols) + 2}"
            params = params + (expected_version,)
        rows_affected = self._db.execute_write_returning_rowcount(sql, params)
        if rows_affected == 0 and expected_version is not None:
            current = self._fetch_version(entity.id)
            raise CASMismatch(
                f"expected_version={expected_version} but stored version={current}"
            )

    def get_by_id(self, id: str) -> EnvSectionSchema | None:
        rows = self._db.execute_query(
            f"SELECT * FROM {self._table} WHERE ID = :p0", (id,),
        )
        return self._row_to_entity(rows[0]) if rows else None

    def get_by_natural_key(
        self, *, tenant_id: str, section: str
    ) -> EnvSectionSchema | None:
        rows = self._db.execute_query(
            f"SELECT * FROM {self._table} "
            f"WHERE TENANT_ID = :p0 AND SECTION = :p1 AND IS_ACTIVE = 1",
            (tenant_id, section or ""),
        )
        return self._row_to_entity(rows[0]) if rows else None

    def list_paged(
        self, filters: ListEnvSectionSchemasFilter
    ) -> ListEnvSectionSchemasPage:
        conditions: list[str] = ["TENANT_ID = :p0"]
        params: list = [filters.tenant_id]
        if not filters.include_inactive:
            conditions.append(f"IS_ACTIVE = :p{len(params)}")
            params.append(1)
        if filters.section:
            conditions.append(f"SECTION = :p{len(params)}")
            params.append(filters.section)
        where = f"WHERE {' AND '.join(conditions)}"

        count_sql = f"SELECT COUNT(*) AS CNT FROM {self._table} {where}"
        total_rows = self._db.execute_query(count_sql, tuple(params))
        total = int(total_rows[0]["cnt"]) if total_rows else 0

        limit_param = f":p{len(params)}"
        params.append(filters.top)
        offset_param = f":p{len(params)}"
        params.append(filters.skip)
        page_sql = (
            f"SELECT * FROM {self._table} {where} "
            f"ORDER BY SECTION LIMIT {limit_param} OFFSET {offset_param}"
        )
        rows = self._db.execute_query(page_sql, tuple(params))
        return ListEnvSectionSchemasPage(
            items=[self._row_to_entity(r) for r in rows], total=total
        )

    def soft_delete(self, id: str) -> None:
        self._db.execute_write(
            f"UPDATE {self._table} SET IS_ACTIVE = 0, VERSION = VERSION + 1 WHERE ID = :p0",
            (id,),
        )

    def _row_to_entity(self, row: dict) -> EnvSectionSchema:
        # execute_query lowercases result column names.
        keys = json.loads(row.get("keys") or "[]")
        return EnvSectionSchema(
            id=row["id"],
            tenant_id=row["tenant_id"],
            section=row.get("section") or "",
            keys=keys,
            is_active=bool(int(row.get("is_active") or 0)),
            created_by=row.get("created_by") or "",
            created_at=row.get("created_at") or "",
            updated_by=row.get("updated_by") or "",
            updated_at=row.get("updated_at") or "",
            version=int(row.get("version") or 1),
        )
