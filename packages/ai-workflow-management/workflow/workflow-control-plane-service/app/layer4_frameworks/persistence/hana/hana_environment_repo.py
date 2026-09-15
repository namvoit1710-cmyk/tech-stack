"""SAP HANA implementation of IEnvironmentRepo (table AIW_ENVIRONMENT, SA-2365).

Mirrors ``HanaGatewayAppRepo``: raw SQL through the shared ``db`` handle, CAS on
the VERSION column, soft-delete via IS_ACTIVE = 0. Two env-specific differences:

* every read is tenant-scoped (``list_paged`` filters TENANT_ID) — an Environment
  is per-tenant, unlike a globally-named gateway app;
* the natural key (TENANT_ID, SECTION, NAME) is enforced here for ACTIVE rows
  only, so a soft-deleted environment can be recreated. This matches
  ``MemoryEnvironmentRepo``; the migration therefore uses a non-unique index.

VARIABLES is stored as a JSON array of ``{key, type, value, values}``. A
credential-type variable carries only a credential_ref id in ``value`` — never a
secret — so nothing sensitive is persisted here.
"""
from __future__ import annotations

import json
from typing import Any

from app.layer1_domain.entities.environment import Environment
from app.layer2_application.repositories.environment_repo_interface import (
    ListEnvironmentsFilter,
    ListEnvironmentsPage,
)
from app.layer2_application.repositories.gateway_app_repo_interface import CASMismatch
from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin


class HanaEnvironmentRepo(AsyncRepoMixin):
    def __init__(self, db: Any) -> None:
        self._db = db
        self._table = "AIW_ENVIRONMENT"

    _DATA_COLUMNS = (
        "ID", "TENANT_ID", "SECTION", "NAME", "VARIABLES", "IS_ACTIVE",
        "CREATED_BY", "CREATED_AT", "UPDATED_BY", "UPDATED_AT",
    )

    @staticmethod
    def _dump_variables(entity: Environment) -> str:
        return json.dumps([
            {
                "key": v.key,
                "type": getattr(v.type, "value", v.type),
                "value": v.value,
                "values": list(v.values),
            }
            for v in entity.variables
        ])

    def _data_values(self, entity: Environment) -> tuple:
        return (
            entity.id,
            entity.tenant_id,
            entity.section or "",
            entity.name,
            self._dump_variables(entity),
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

    def _assert_natural_key_free(self, entity: Environment) -> None:
        """Reject a second ACTIVE row for the same (tenant, section, name)."""
        rows = self._db.execute_query(
            f"SELECT ID FROM {self._table} "
            f"WHERE TENANT_ID = :p0 AND SECTION = :p1 AND NAME = :p2 "
            f"AND IS_ACTIVE = 1 AND ID <> :p3",
            (entity.tenant_id, entity.section or "", entity.name, entity.id),
        )
        if rows:
            raise CASMismatch(
                f"environment ({entity.tenant_id}/{entity.section}/{entity.name}) already exists"
            )

    def save(self, entity: Environment, *, expected_version: int | None = None) -> None:
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

    def get_by_id(self, id: str) -> Environment | None:
        rows = self._db.execute_query(
            f"SELECT * FROM {self._table} WHERE ID = :p0", (id,),
        )
        return self._row_to_entity(rows[0]) if rows else None

    def get_by_natural_key(
        self, *, tenant_id: str, section: str, name: str
    ) -> Environment | None:
        rows = self._db.execute_query(
            f"SELECT * FROM {self._table} "
            f"WHERE TENANT_ID = :p0 AND SECTION = :p1 AND NAME = :p2 AND IS_ACTIVE = 1",
            (tenant_id, section or "", name),
        )
        return self._row_to_entity(rows[0]) if rows else None

    def list_paged(self, filters: ListEnvironmentsFilter) -> ListEnvironmentsPage:
        conditions: list[str] = ["TENANT_ID = :p0"]
        params: list = [filters.tenant_id]
        if not filters.include_inactive:
            conditions.append(f"IS_ACTIVE = :p{len(params)}")
            params.append(1)
        if filters.section:
            conditions.append(f"SECTION = :p{len(params)}")
            params.append(filters.section)
        if filters.name_contains:
            conditions.append(f"LOWER(NAME) LIKE :p{len(params)}")
            params.append(f"%{filters.name_contains.lower()}%")
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
            f"ORDER BY SECTION, NAME LIMIT {limit_param} OFFSET {offset_param}"
        )
        rows = self._db.execute_query(page_sql, tuple(params))
        return ListEnvironmentsPage(
            items=[self._row_to_entity(r) for r in rows], total=total
        )

    def soft_delete(self, id: str) -> None:
        self._db.execute_write(
            f"UPDATE {self._table} SET IS_ACTIVE = 0, VERSION = VERSION + 1 WHERE ID = :p0",
            (id,),
        )

    def _row_to_entity(self, row: dict) -> Environment:
        # execute_query lowercases result column names.
        variables = json.loads(row.get("variables") or "[]")
        return Environment(
            id=row["id"],
            tenant_id=row["tenant_id"],
            section=row.get("section") or "",
            name=row["name"],
            variables=variables,
            is_active=bool(int(row.get("is_active") or 0)),
            created_by=row.get("created_by") or "",
            created_at=row.get("created_at") or "",
            updated_by=row.get("updated_by") or "",
            updated_at=row.get("updated_at") or "",
            version=int(row.get("version") or 1),
        )
