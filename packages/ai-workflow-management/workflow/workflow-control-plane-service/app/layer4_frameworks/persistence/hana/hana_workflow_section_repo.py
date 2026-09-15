"""SAP HANA implementation of IWorkflowSectionRepo (AIW_WORKFLOW_SECTION, SA-2605).

Mirrors HanaWorkflowEnvironmentRepo: raw SQL through the shared ``db`` handle, CAS on
the VERSION column, soft-delete via IS_ACTIVE = 0. One ACTIVE row per
(TENANT_ID, WORKFLOW_ID) is enforced here for ACTIVE rows only (so a soft-deleted
binding can be recreated) — the migration uses a non-unique index.
"""
from __future__ import annotations

from typing import Any

from app.layer1_domain.entities.workflow_section import WorkflowSection
from app.layer2_application.repositories.gateway_app_repo_interface import CASMismatch
from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin


class HanaWorkflowSectionRepo(AsyncRepoMixin):
    def __init__(self, db: Any) -> None:
        self._db = db
        self._table = "AIW_WORKFLOW_SECTION"

    _DATA_COLUMNS = (
        "ID", "TENANT_ID", "WORKFLOW_ID", "SECTION", "IS_ACTIVE",
        "CREATED_BY", "CREATED_AT", "UPDATED_BY", "UPDATED_AT",
    )

    def _data_values(self, entity: WorkflowSection) -> tuple:
        return (
            entity.id,
            entity.tenant_id,
            entity.workflow_id,
            entity.section or "",
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

    def _assert_workflow_free(self, entity: WorkflowSection) -> None:
        rows = self._db.execute_query(
            f"SELECT ID FROM {self._table} "
            f"WHERE TENANT_ID = :p0 AND WORKFLOW_ID = :p1 AND IS_ACTIVE = 1 AND ID <> :p2",
            (entity.tenant_id, entity.workflow_id, entity.id),
        )
        if rows:
            raise CASMismatch(
                f"workflow {entity.workflow_id} already has a section binding"
            )

    def save(
        self,
        entity: WorkflowSection,
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
                self._assert_workflow_free(entity)
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
            self._assert_workflow_free(entity)

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

    def get_by_workflow(
        self, *, tenant_id: str, workflow_id: str
    ) -> WorkflowSection | None:
        rows = self._db.execute_query(
            f"SELECT * FROM {self._table} "
            f"WHERE TENANT_ID = :p0 AND WORKFLOW_ID = :p1 AND IS_ACTIVE = 1",
            (tenant_id, workflow_id),
        )
        return self._row_to_entity(rows[0]) if rows else None

    def list_by_section(
        self, *, tenant_id: str, section: str
    ) -> list[WorkflowSection]:
        rows = self._db.execute_query(
            f"SELECT * FROM {self._table} "
            f"WHERE TENANT_ID = :p0 AND SECTION = :p1 AND IS_ACTIVE = 1 "
            f"ORDER BY WORKFLOW_ID",
            (tenant_id, section),
        )
        return [self._row_to_entity(r) for r in rows]

    def soft_delete_by_workflow(self, *, tenant_id: str, workflow_id: str) -> None:
        self._db.execute_write(
            f"UPDATE {self._table} SET IS_ACTIVE = 0, VERSION = VERSION + 1 "
            f"WHERE TENANT_ID = :p0 AND WORKFLOW_ID = :p1 AND IS_ACTIVE = 1",
            (tenant_id, workflow_id),
        )

    def clear_section(self, *, tenant_id: str, section: str, updated_by: str) -> int:
        return self._db.execute_write_returning_rowcount(
            f"UPDATE {self._table} SET IS_ACTIVE = 0, VERSION = VERSION + 1, UPDATED_BY = :p0 "
            f"WHERE TENANT_ID = :p1 AND SECTION = :p2 AND IS_ACTIVE = 1",
            (updated_by, tenant_id, section),
        )

    def _row_to_entity(self, row: dict) -> WorkflowSection:
        # execute_query lowercases result column names.
        return WorkflowSection(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workflow_id=row["workflow_id"],
            section=row.get("section") or "",
            is_active=bool(int(row.get("is_active") or 0)),
            created_by=row.get("created_by") or "",
            created_at=row.get("created_at") or "",
            updated_by=row.get("updated_by") or "",
            updated_at=row.get("updated_at") or "",
            version=int(row.get("version") or 1),
        )
