"""SAP HANA implementation of IGatewayFunctionRepo (table AIW_GATEWAY_FUNCTIONS).

CAS on update via the VERSION column. INPUT_SCHEMA / OUTPUT_SCHEMA / TAGS are
stored as JSON (NCLOB).
"""
from __future__ import annotations

import json
from typing import Any

from app.layer1_domain.entities.gateway_function import GatewayFunction
from app.layer1_domain.value_objects.gateway.function_param import FunctionParam
from app.layer1_domain.value_objects.gateway.http_method import HttpMethod
from app.layer1_domain.value_objects.gateway.output_field import OutputField
from app.layer1_domain.value_objects.gateway.path_template import PathTemplate
from app.layer2_application.repositories.gateway_function_repo_interface import (
    CASMismatch,
    IGatewayFunctionRepo,
    ListGatewayFunctionsFilter,
    ListGatewayFunctionsPage,
)
from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin


class HanaGatewayFunctionRepo(AsyncRepoMixin, IGatewayFunctionRepo):
    def __init__(self, db: Any) -> None:
        self._db = db
        self._table = "AIW_GATEWAY_FUNCTIONS"

    _DATA_COLUMNS = (
        "ID", "APP_ID", "NAME", "DESCRIPTION", "METHOD", "PATH_TEMPLATE",
        "INPUT_SCHEMA", "OUTPUT_SCHEMA", "TIMEOUT_MS", "ICON", "COLOR", "TAGS",
        "IS_ACTIVE",
        "CREATED_BY", "CREATED_AT", "UPDATED_BY", "UPDATED_AT",
    )

    def _data_values(self, entity: GatewayFunction) -> tuple:
        return (
            entity.id,
            entity.app_id,
            entity.name,
            entity.description or "",
            entity.method.value,
            entity.path_template.value if entity.path_template else "",
            json.dumps([p.to_dict() for p in entity.input_params]),
            json.dumps([o.to_dict() for o in entity.output_fields]),
            int(entity.timeout_ms),
            entity.icon or "",
            entity.color or "",
            json.dumps(list(entity.tags)),
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

    def save(
        self, entity: GatewayFunction, *, expected_version: int | None = None
    ) -> None:
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

    def get_by_id(self, id: str) -> GatewayFunction | None:
        rows = self._db.execute_query(
            f"SELECT * FROM {self._table} WHERE ID = :p0", (id,),
        )
        return self._row_to_entity(rows[0]) if rows else None

    def get_by_app_and_name(self, app_id: str, name: str) -> GatewayFunction | None:
        rows = self._db.execute_query(
            f"SELECT * FROM {self._table} WHERE APP_ID = :p0 AND NAME = :p1",
            (app_id, name),
        )
        return self._row_to_entity(rows[0]) if rows else None

    def list_by_app(
        self, filters: ListGatewayFunctionsFilter
    ) -> ListGatewayFunctionsPage:
        conditions = ["APP_ID = :p0"]
        params: list = [filters.app_id]
        if not filters.include_inactive:
            conditions.append(f"IS_ACTIVE = :p{len(params)}")
            params.append(1)
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
            f"ORDER BY NAME LIMIT {limit_param} OFFSET {offset_param}"
        )
        rows = self._db.execute_query(page_sql, tuple(params))
        return ListGatewayFunctionsPage(
            items=[self._row_to_entity(r) for r in rows], total=total
        )

    def soft_delete(self, id: str) -> None:
        self._db.execute_write(
            f"UPDATE {self._table} SET IS_ACTIVE = 0, VERSION = VERSION + 1 WHERE ID = :p0",
            (id,),
        )

    def _row_to_entity(self, row: dict) -> GatewayFunction:
        input_params = tuple(
            FunctionParam.from_dict(d) for d in json.loads(row.get("input_schema") or "[]")
        )
        output_fields = tuple(
            OutputField.from_dict(d) for d in json.loads(row.get("output_schema") or "[]")
        )
        tags = tuple(json.loads(row.get("tags") or "[]"))
        return GatewayFunction(
            id=row["id"],
            app_id=row["app_id"],
            name=row["name"],
            description=row.get("description") or "",
            method=HttpMethod(row["method"]),
            path_template=PathTemplate(row["path_template"]),
            input_params=input_params,
            output_fields=output_fields,
            timeout_ms=int(row.get("timeout_ms") or 0),
            icon=row.get("icon") or "",
            color=row.get("color") or "",
            tags=tags,
            is_active=bool(int(row.get("is_active") or 0)),
            created_by=row.get("created_by") or "",
            created_at=row.get("created_at") or "",
            updated_by=row.get("updated_by") or "",
            updated_at=row.get("updated_at") or "",
            version=int(row.get("version") or 1),
        )
