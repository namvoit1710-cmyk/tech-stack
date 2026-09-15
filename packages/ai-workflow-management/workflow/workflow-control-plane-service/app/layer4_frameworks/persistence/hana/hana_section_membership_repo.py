"""SAP HANA implementation of ISectionMembershipRepo (AIW_SECTION_ENVIRONMENT,
SA-2605).

Mirrors HanaWorkflowEnvironmentRepo: raw SQL through the shared ``db`` handle, CAS on
the VERSION column, soft-delete via IS_ACTIVE = 0. One ACTIVE row per
(TENANT_ID, SECTION) enforced here for ACTIVE rows only. ENVIRONMENT_IDS is a JSON
array of AIW_ENVIRONMENT ids.
"""
from __future__ import annotations

import json
from typing import Any

from app.layer1_domain.entities.section_membership import SectionMembership
from app.layer2_application.repositories.gateway_app_repo_interface import CASMismatch
from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin


class HanaSectionMembershipRepo(AsyncRepoMixin):
    def __init__(self, db: Any) -> None:
        self._db = db
        self._table = "AIW_SECTION_ENVIRONMENT"

    _DATA_COLUMNS = (
        "ID", "TENANT_ID", "SECTION", "ENVIRONMENT_IDS", "IS_ACTIVE",
        "CREATED_BY", "CREATED_AT", "UPDATED_BY", "UPDATED_AT",
    )

    def _data_values(self, entity: SectionMembership) -> tuple:
        return (
            entity.id,
            entity.tenant_id,
            entity.section,
            json.dumps(list(entity.environment_ids)),
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

    def _assert_section_free(self, entity: SectionMembership) -> None:
        rows = self._db.execute_query(
            f"SELECT ID FROM {self._table} "
            f"WHERE TENANT_ID = :p0 AND SECTION = :p1 AND IS_ACTIVE = 1 AND ID <> :p2",
            (entity.tenant_id, entity.section, entity.id),
        )
        if rows:
            raise CASMismatch(
                f"section {entity.section} already has a membership row"
            )

    def save(
        self,
        entity: SectionMembership,
        *,
        expected_version: int | None = None,
    ) -> None:
        existing_version = self._fetch_version(entity.id)

        if existing_version is None:
            if expected_version is not None:
                raise CASMismatch(
                    f"expected_version={expected_version} but no row exists for id={entity.id}"
                )
            if entity.is_active:
                self._assert_section_free(entity)
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
            self._assert_section_free(entity)

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

    def get_by_section(
        self, *, tenant_id: str, section: str
    ) -> SectionMembership | None:
        rows = self._db.execute_query(
            f"SELECT * FROM {self._table} "
            f"WHERE TENANT_ID = :p0 AND SECTION = :p1 AND IS_ACTIVE = 1",
            (tenant_id, section),
        )
        return self._row_to_entity(rows[0]) if rows else None

    def list_all(self, *, tenant_id: str) -> list[SectionMembership]:
        rows = self._db.execute_query(
            f"SELECT * FROM {self._table} WHERE TENANT_ID = :p0 AND IS_ACTIVE = 1 "
            f"ORDER BY SECTION",
            (tenant_id,),
        )
        return [self._row_to_entity(r) for r in rows]

    def list_sections_for_environment(
        self, *, tenant_id: str, environment_id: str
    ) -> list[str]:
        # ENVIRONMENT_IDS is a JSON array; filter in-process after a tenant scan
        # (one row per section — small).
        rows = self._db.execute_query(
            f"SELECT * FROM {self._table} WHERE TENANT_ID = :p0 AND IS_ACTIVE = 1 "
            f"ORDER BY SECTION",
            (tenant_id,),
        )
        out = [self._row_to_entity(r) for r in rows]
        return [m.section for m in out if environment_id in m.environment_ids]

    def soft_delete_by_section(self, *, tenant_id: str, section: str) -> None:
        self._db.execute_write(
            f"UPDATE {self._table} SET IS_ACTIVE = 0, VERSION = VERSION + 1 "
            f"WHERE TENANT_ID = :p0 AND SECTION = :p1 AND IS_ACTIVE = 1",
            (tenant_id, section),
        )

    def _row_to_entity(self, row: dict) -> SectionMembership:
        # execute_query lowercases result column names.
        env_ids = json.loads(row.get("environment_ids") or "[]")
        return SectionMembership(
            id=row["id"],
            tenant_id=row["tenant_id"],
            section=row.get("section") or "",
            environment_ids=env_ids,
            is_active=bool(int(row.get("is_active") or 0)),
            created_by=row.get("created_by") or "",
            created_at=row.get("created_at") or "",
            updated_by=row.get("updated_by") or "",
            updated_at=row.get("updated_at") or "",
            version=int(row.get("version") or 1),
        )
