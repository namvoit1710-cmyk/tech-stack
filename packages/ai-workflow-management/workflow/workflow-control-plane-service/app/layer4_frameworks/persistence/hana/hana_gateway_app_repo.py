"""SAP HANA implementation of IGatewayAppRepo (table AIW_GATEWAY_APPS).

CAS on update via the VERSION column (same pattern as the v1 gateway repo).
Soft-delete writes IS_ACTIVE = 0 and bumps VERSION.
"""
from __future__ import annotations

import json
from typing import Any

from app.layer1_domain.entities.gateway_app import GatewayApp
from app.layer1_domain.value_objects.auth_type import AuthType
from app.layer1_domain.value_objects.encrypted_credential import EncryptedCredential
from app.layer1_domain.value_objects.gateway.app_kind import AppKind
from app.layer1_domain.value_objects.gateway.host_allowlist import HostAllowlist
from app.layer2_application.repositories.gateway_app_repo_interface import (
    CASMismatch,
    IGatewayAppRepo,
    ListGatewayAppsFilter,
    ListGatewayAppsPage,
)
from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin


class HanaGatewayAppRepo(AsyncRepoMixin, IGatewayAppRepo):
    def __init__(self, db: Any) -> None:
        self._db = db
        self._table = "AIW_GATEWAY_APPS"

    _DATA_COLUMNS = (
        "ID", "NAME", "KIND", "BASE_URL", "ALLOWED_HOSTS",
        "AUTH_TYPE", "AUTH_CONFIG_ENCRYPTED", "AUTH_CONFIG_KEY_VERSION",
        "IS_ACTIVE", "DESCRIPTION",
        "CREATED_BY", "CREATED_AT", "UPDATED_BY", "UPDATED_AT",
        "SPEC_URL", "LAST_SYNCED_AT", "SPEC_HASH",
    )

    def _data_values(self, entity: GatewayApp) -> tuple:
        cred = entity.auth_config_encrypted
        hosts = entity.allowed_hosts.to_list() if entity.allowed_hosts else []
        return (
            entity.id,
            entity.name,
            entity.kind.value,
            entity.base_url or "",
            json.dumps(hosts),
            entity.auth_type.value,
            cred.ciphertext_b64 if cred else "",
            cred.key_version if cred else 0,
            1 if entity.is_active else 0,
            entity.description or "",
            entity.created_by or "",
            entity.created_at or "",
            entity.updated_by or "",
            entity.updated_at or "",
            entity.spec_url or "",
            entity.last_synced_at or "",
            entity.spec_hash or "",
        )

    def _fetch_version(self, id: str) -> int | None:
        rows = self._db.execute_query(
            f"SELECT VERSION FROM {self._table} WHERE ID = :p0", (id,),
        )
        return int(rows[0]["version"]) if rows else None

    def save(self, entity: GatewayApp, *, expected_version: int | None = None) -> None:
        existing_version = self._fetch_version(entity.id)
        if existing_version is None:
            if expected_version is not None:
                raise CASMismatch(
                    f"expected_version={expected_version} but no row exists for id={entity.id}"
                )
            cols = ", ".join(self._DATA_COLUMNS) + ", VERSION"
            placeholders = ", ".join(f":p{i}" for i in range(len(self._DATA_COLUMNS) + 1))
            sql = f"INSERT INTO {self._table} ({cols}) VALUES ({placeholders})"
            self._db.execute_write(sql, self._data_values(entity) + (entity.version,))
            return

        if expected_version is not None and existing_version != expected_version:
            raise CASMismatch(
                f"expected_version={expected_version} but stored version={existing_version}"
            )

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

    def get_by_id(self, id: str) -> GatewayApp | None:
        rows = self._db.execute_query(
            f"SELECT * FROM {self._table} WHERE ID = :p0", (id,),
        )
        return self._row_to_entity(rows[0]) if rows else None

    def get_by_name(self, name: str) -> GatewayApp | None:
        rows = self._db.execute_query(
            f"SELECT * FROM {self._table} WHERE NAME = :p0", (name,),
        )
        return self._row_to_entity(rows[0]) if rows else None

    def list_paged(self, filters: ListGatewayAppsFilter) -> ListGatewayAppsPage:
        conditions: list[str] = []
        params: list = []
        if not filters.include_inactive:
            conditions.append(f"IS_ACTIVE = :p{len(params)}")
            params.append(1)
        if filters.name_contains:
            conditions.append(f"LOWER(NAME) LIKE :p{len(params)}")
            params.append(f"%{filters.name_contains.lower()}%")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        count_sql = f"SELECT COUNT(*) AS CNT FROM {self._table} {where}"
        total_rows = self._db.execute_query(count_sql, tuple(params))
        total = int(total_rows[0]["cnt"]) if total_rows else 0

        limit_param = f":p{len(params)}"
        params.append(filters.top)
        offset_param = f":p{len(params)}"
        params.append(filters.skip)
        page_sql = (
            f"SELECT * FROM {self._table} {where} "
            f"ORDER BY NAME LIMIT {limit_param} OFFSET {offset_param}"
        )
        rows = self._db.execute_query(page_sql, tuple(params))
        return ListGatewayAppsPage(
            items=[self._row_to_entity(r) for r in rows], total=total
        )

    def soft_delete(self, id: str) -> None:
        self._db.execute_write(
            f"UPDATE {self._table} SET IS_ACTIVE = 0, VERSION = VERSION + 1 WHERE ID = :p0",
            (id,),
        )

    def _row_to_entity(self, row: dict) -> GatewayApp:
        # execute_query lowercases result column names.
        ciphertext = row.get("auth_config_encrypted") or ""
        cred = (
            EncryptedCredential(
                ciphertext_b64=ciphertext,
                key_version=int(row.get("auth_config_key_version") or 0),
            )
            if ciphertext
            else None
        )
        hosts = json.loads(row.get("allowed_hosts") or "[]")
        return GatewayApp(
            id=row["id"],
            name=row["name"],
            kind=AppKind(row["kind"]),
            base_url=row.get("base_url") or "",
            allowed_hosts=HostAllowlist.from_list(hosts) if hosts else None,
            auth_type=AuthType(row["auth_type"]),
            auth_config_encrypted=cred,
            is_active=bool(int(row.get("is_active") or 0)),
            description=row.get("description") or "",
            created_by=row.get("created_by") or "",
            created_at=row.get("created_at") or "",
            updated_by=row.get("updated_by") or "",
            updated_at=row.get("updated_at") or "",
            spec_url=row.get("spec_url") or "",
            last_synced_at=row.get("last_synced_at") or "",
            spec_hash=row.get("spec_hash") or "",
            version=int(row.get("version") or 1),
        )
