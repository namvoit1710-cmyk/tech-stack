"""SAP HANA implementation of INodeDefinitionRepository.

Persists NodeDefinition entities to AIW_NODE_DEFINITIONS table.
JSON fields are stored in NCLOB columns.
"""
import json
from typing import Any

from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.repositories.node_definition_repository_interface import INodeDefinitionRepository


from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin


class HanaNodeDefinitionRepository(AsyncRepoMixin, INodeDefinitionRepository):

    def __init__(self, db: Any) -> None:
        self._db = db
        self._table = "AIW_NODE_DEFINITIONS"

    def _to_json(self, obj: Any) -> str | None:
        if obj is None:
            return None
        if isinstance(obj, str):
            return obj
        return json.dumps(obj, default=str)

    def _from_json(self, raw: Any) -> Any:
        if raw is None:
            return None
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    def _execute_query(self, sql: str, params: tuple = ()) -> list[dict]:
        return self._db.execute_query(sql, params)

    def _execute_write(self, sql: str, params: tuple = ()) -> None:
        self._db.execute_write(sql, params)

    def _upsert(self, columns: list[str], values: tuple) -> None:
        placeholders = ", ".join(f":p{i}" for i in range(len(values)))
        col_str = ", ".join(columns)
        sql = (
            f"UPSERT {self._table} ({col_str}) VALUES ({placeholders}) "
            f"WITH PRIMARY KEY"
        )
        self._execute_write(sql, values)

    def save(self, node_definition: NodeDefinition) -> None:
        columns = [
            "ID", "TENANT_ID", "NAME", "DESCRIPTION",
            "PARENT_DEFINITION_ID",
            "BASE_TYPE", "BASE_WORKER_ID",
            "LOCKED_DEFAULTS", "INPUT_SCHEMA", "OUTPUT_SCHEMA",
            "INPUT_MAPPING", "OUTPUT_MAPPING",
            "ICON", "COLOR", "TAGS", "PORTS",
            "DATA_MODE", "EDIT_MODE",
            "KIND", "COLLECTION",
            "CREATED_AT", "UPDATED_AT",
        ]
        base_type = node_definition.base_type.value if hasattr(node_definition.base_type, "value") else str(node_definition.base_type)
        values = (
            node_definition.id,
            node_definition.tenant_id,
            node_definition.name,
            node_definition.description,
            node_definition.parent_definition_id,
            base_type,
            node_definition.base_worker_id,
            self._to_json(node_definition.locked_defaults),
            self._to_json(node_definition.input_schema),
            self._to_json(node_definition.output_schema),
            self._to_json(node_definition.input_mapping),
            self._to_json(node_definition.output_mapping),
            node_definition.icon,
            node_definition.color,
            self._to_json(node_definition.tags),
            self._to_json(node_definition.ports),
            node_definition.data_mode,
            node_definition.edit_mode,
            node_definition.kind,
            node_definition.collection,
            node_definition.created_at,
            node_definition.updated_at,
        )
        self._upsert(columns, values)

    def get_by_id(self, id: str) -> NodeDefinition | None:
        rows = self._execute_query(
            f"SELECT * FROM {self._table} WHERE ID = :p0", (id,)
        )
        if not rows:
            return None
        return self._row_to_entity(rows[0])

    def list_by_tenant(self, tenant_id: str) -> list[NodeDefinition]:
        rows = self._execute_query(
            f"SELECT * FROM {self._table} WHERE TENANT_ID = :p0", (tenant_id,)
        )
        return [self._row_to_entity(r) for r in rows]

    def delete(self, id: str) -> None:
        self._execute_write(
            f"DELETE FROM {self._table} WHERE ID = :p0", (id,)
        )

    def delete_all(self) -> int:
        count_rows = self._execute_query(f"SELECT COUNT(*) AS cnt FROM {self._table}")
        count = count_rows[0]["cnt"] if count_rows else 0
        self._execute_write(f"DELETE FROM {self._table}")
        return count

    def list_missing_taxonomy(self, tenant_id: str, limit: int) -> list[NodeDefinition]:
        """Legacy rows where KIND/COLLECTION is still NULL or empty (F/SA-1740 backfill).

        Only these rows are touched by the backfill, so a user-set value is never
        clobbered (AC4). ``_row_to_entity`` heals the returned entities on read.

        FIX4 (SECURITY): tenant-scoped and bounded. This used to run with no
        ``TENANT_ID`` filter and no ``LIMIT`` — any caller could rewrite every
        tenant's rows in one unbounded, fully-materialised query. Now callers
        must page through one tenant at a time.
        """
        rows = self._execute_query(
            f"SELECT * FROM {self._table} "
            f"WHERE TENANT_ID = :p0 "
            f"AND (KIND IS NULL OR KIND = '' OR COLLECTION IS NULL OR COLLECTION = '') "
            f"LIMIT :p1",
            (tenant_id, limit),
        )
        return [self._row_to_entity(r) for r in rows]

    def _row_to_entity(self, row: dict) -> NodeDefinition:
        return NodeDefinition(
            id=row["id"],
            tenant_id=row.get("tenant_id", ""),
            name=row.get("name", ""),
            description=row.get("description", ""),
            parent_definition_id=row.get("parent_definition_id"),
            base_type=NodeType(row.get("base_type", "TASK")),
            base_worker_id=row.get("base_worker_id"),
            locked_defaults=self._from_json(row.get("locked_defaults")) or {},
            input_schema=self._from_json(row.get("input_schema")) or [],
            output_schema=self._from_json(row.get("output_schema")) or [],
            input_mapping=self._from_json(row.get("input_mapping")) or {},
            output_mapping=self._from_json(row.get("output_mapping")) or {},
            icon=row.get("icon", ""),
            color=row.get("color", ""),
            tags=self._from_json(row.get("tags")) or [],
            ports=self._from_json(row.get("ports")) or {},
            data_mode=row.get("data_mode", "json"),
            edit_mode=row.get("edit_mode", "none"),
            # KIND/COLLECTION are nullable (added later); NULL/missing -> "" so the
            # entity's __post_init__ heals (kind via default_kind, collection -> "My Nodes").
            kind=row.get("kind") or "",
            collection=row.get("collection") or "",
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
        )
