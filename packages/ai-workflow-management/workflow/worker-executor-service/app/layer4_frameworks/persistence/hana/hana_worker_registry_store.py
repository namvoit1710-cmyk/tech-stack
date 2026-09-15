"""SAP HANA implementation of IWorkerRegistryStore."""
from datetime import datetime, timezone
import json
from typing import TYPE_CHECKING

from app.layer1_domain.entities.worker_registration import WorkerRegistration, WorkerCapability
from app.layer1_domain.value_objects import app_instance_aggregation

if TYPE_CHECKING:
    from app.layer2_application.interfaces.database_connection_interface import IDatabaseConnection


class HanaWorkerRegistryStore:
    """SAP HANA-backed worker registry for production use."""

    TABLE_NAME = "AIW_WORKER_REGISTRATIONS"

    def __init__(self, db: "IDatabaseConnection") -> None:
        self._db = db

    def register(self, worker: WorkerRegistration) -> None:
        # If worker_type is set, upsert: reuse existing registration ID
        if worker.worker_type:
            existing = self.get_by_worker_type(worker.worker_type)
            if existing:
                worker.id = existing.id

        capabilities_json = json.dumps([
            {"domain": c.domain, "action": c.action}
            for c in worker.capabilities
        ])
        input_schema_json = json.dumps(worker.input_schema) if worker.input_schema else None
        output_schema_json = json.dumps(worker.output_schema) if worker.output_schema else None
        tags_json = json.dumps(worker.tags) if worker.tags else "[]"
        ports_json = json.dumps(worker.ports) if worker.ports else '{"in": [], "out": []}'

        sql = f"""
            UPSERT {self.TABLE_NAME} (
                ID, NAME, CAPABILITIES, ENDPOINT_URL,
                HEALTH_STATUS, LAST_HEARTBEAT, TTL_SECONDS,
                WORKER_TYPE, VERSION, SDK_VERSION,
                INPUT_SCHEMA, OUTPUT_SCHEMA,
                DESCRIPTION, NODE_CLASS, ICON, COLOR, TAGS, PORTS
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            WITH PRIMARY KEY
        """
        self._db.execute_write(sql, (
            worker.id,
            worker.name,
            capabilities_json,
            worker.endpoint_url,
            worker.health_status,
            worker.last_heartbeat,
            worker.ttl_seconds,
            worker.worker_type,
            worker.version,
            worker.sdk_version,
            input_schema_json,
            output_schema_json,
            worker.description,
            worker.node_class,
            worker.icon,
            worker.color,
            tags_json,
            ports_json,
        ))

    def heartbeat(self, worker_id: str, status: str = "healthy") -> bool:
        check_sql = f"SELECT 1 FROM {self.TABLE_NAME} WHERE ID = ?"
        rows = self._db.execute_query(check_sql, (worker_id,))
        if not rows:
            return False
        now = datetime.now(timezone.utc).isoformat()
        sql = f"""
            UPDATE {self.TABLE_NAME}
            SET LAST_HEARTBEAT = ?, HEALTH_STATUS = ?
            WHERE ID = ?
        """
        self._db.execute_write(sql, (now, status, worker_id))
        return True

    def discover(self, capability: str) -> list[WorkerRegistration]:
        sql = f"SELECT * FROM {self.TABLE_NAME}"
        rows = self._db.execute_query(sql)

        if not capability:
            return [self._row_to_entity(row) for row in rows]

        results = []
        for row in rows:
            worker = self._row_to_entity(row)
            for cap in worker.capabilities:
                if cap.domain == capability or cap.action == capability:
                    results.append(worker)
                    break
        return results

    def deregister(self, worker_id: str) -> None:
        sql = f"DELETE FROM {self.TABLE_NAME} WHERE ID = ?"
        self._db.execute_write(sql, (worker_id,))

    def get(self, worker_id: str) -> WorkerRegistration | None:
        sql = f"SELECT * FROM {self.TABLE_NAME} WHERE ID = ?"
        rows = self._db.execute_query(sql, (worker_id,))
        if not rows:
            return None
        return self._row_to_entity(rows[0])

    def get_by_worker_type(self, worker_type: str) -> WorkerRegistration | None:
        if not worker_type:
            return None
        sql = f"SELECT * FROM {self.TABLE_NAME} WHERE WORKER_TYPE = ?"
        rows = self._db.execute_query(sql, (worker_type,))
        if not rows:
            return None
        return self._row_to_entity(rows[0])

    def list_all(self) -> list[WorkerRegistration]:
        sql = f"SELECT * FROM {self.TABLE_NAME}"
        rows = self._db.execute_query(sql)
        return [self._row_to_entity(row) for row in rows]

    def app_summary(self) -> dict[str, dict]:
        """``{worker_app: {nodes_total, nodes_healthy, replicas_total,
        replicas_healthy, all_synthesized}}`` — the read behind /workers/apps
        (SA-2047 Task 6).

        Loads rows the same way ``list_all``/``discover`` do (``SELECT *`` then
        ``_row_to_entity`` per row) — this store has no WHERE-filtered read
        today and inventing one here would be a second query style for the
        same table. The counting rule itself (nodes are REGISTRATIONS, replicas
        are PROCESSES, a synthesized instance_id excluded from the replica sets
        entirely) is NOT re-derived here: it lives once in
        ``app_instance_aggregation.app_summary``, shared with
        ``MemoryWorkerRegistryStore`` so the two stores cannot drift onto two
        different rules — see that module for the full "why".
        """
        sql = f"SELECT * FROM {self.TABLE_NAME}"
        rows = self._db.execute_query(sql)
        registrations = [self._row_to_entity(row) for row in rows]
        return app_instance_aggregation.app_summary(registrations)

    def list_app_instances(self, worker_app: str) -> list[tuple[WorkerRegistration, list[str]]]:
        """One entry per PROCESS of an app: (freshest registration, node types)
        (SA-2047 Task 6).

        Same row-loading as ``app_summary``/``list_all``; the fold itself lives
        in ``app_instance_aggregation.list_app_instances`` (shared with
        ``MemoryWorkerRegistryStore``) so both stores apply the identical
        freshest-wins rule, compared via ``parse_ts`` and never a raw string.
        Blank/unknown ``worker_app`` returns ``[]``.
        """
        if not worker_app:
            return []
        sql = f"SELECT * FROM {self.TABLE_NAME}"
        rows = self._db.execute_query(sql)
        registrations = [self._row_to_entity(row) for row in rows]
        return app_instance_aggregation.list_app_instances(registrations, worker_app)

    def _row_to_entity(self, row: dict) -> WorkerRegistration:
        caps_json = row.get("capabilities") or "[]"
        caps_data = json.loads(caps_json) if isinstance(caps_json, str) else caps_json

        capabilities = [
            WorkerCapability(
                domain=c.get("domain", ""),
                action=c.get("action", ""),
            )
            for c in caps_data
        ]

        input_schema_raw = row.get("input_schema")
        input_schema = (
            json.loads(input_schema_raw) if isinstance(input_schema_raw, str) else input_schema_raw
        )

        output_schema_raw = row.get("output_schema")
        output_schema = (
            json.loads(output_schema_raw) if isinstance(output_schema_raw, str) else output_schema_raw
        )

        tags_raw = row.get("tags") or "[]"
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) else (tags_raw or [])

        ports_raw = row.get("ports") or '{"in": [], "out": []}'
        ports = json.loads(ports_raw) if isinstance(ports_raw, str) else (ports_raw or {"in": [], "out": []})

        return WorkerRegistration(
            id=row["id"],
            name=row.get("name", ""),
            capabilities=capabilities,
            endpoint_url=row.get("endpoint_url", ""),
            health_status=row.get("health_status", "healthy"),
            last_heartbeat=row.get("last_heartbeat", ""),
            ttl_seconds=row.get("ttl_seconds", 60),
            worker_type=row.get("worker_type", ""),
            # SA-2047 R14 (Task 6): the identity fields app_summary/
            # list_app_instances need. `register()` does not persist an
            # INSTANCE_ID/WORKER_APP column yet (this store has zero
            # production callers today — bootstrap wires the memory store —
            # so growing the table schema is a future task's call, not this
            # one's). `or ""` -- not `.get(k, "")` -- because it must fall back
            # for BOTH cases a driver can hand back: the key ABSENT (today,
            # always) and the key present with SQL NULL (once a migration adds
            # the column: an existing row rehydrates as None, and these fields
            # are typed str). `.get(k, "")` only covers the first. Both fall
            # back to the WorkerRegistration defaults ("") exactly as before
            # this mapping existed: `__post_init__` derives instance_id from
            # `id` (instance_synthesized=True) and worker_app from
            # endpoint_url. The mapping is added now so a row that DOES carry
            # them is read honestly instead of silently re-synthesizing a
            # fresh identity on every load.
            #
            # CONSTRAINT this creates for that future migration: the moment
            # instance_id is non-empty, __post_init__ DISCARDS the `id` loaded
            # above from the primary key and recomputes
            # `id = f"{instance_id}:{worker_type}"` -- for every read path
            # through this method (get, list_all, discover), not just these
            # two new ones. `register()`'s upsert-by-type sets
            # `worker.id = existing.id` from a `get_by_worker_type()` read, and
            # `heartbeat()`/`deregister()` build `WHERE ID = ?` from ids
            # obtained the same way -- so a migration that adds INSTANCE_ID
            # without guaranteeing `ID == f"{instance_id}:{worker_type}"` in
            # every stored row would make those writes silently target a
            # non-existent row. See
            # test_row_to_entity_preserves_id_when_instance_id_is_consistent
            # in test_hana_store_app_reads.py.
            instance_id=row.get("instance_id") or "",
            worker_app=row.get("worker_app") or "",
            version=row.get("version", ""),
            sdk_version=row.get("sdk_version", ""),
            input_schema=input_schema,
            output_schema=output_schema,
            description=row.get("description", ""),
            node_class=row.get("node_class", "BUSINESS"),
            icon=row.get("icon", "Cog"),
            color=row.get("color", "#3B82F6"),
            tags=tags,
            ports=ports,
        )
