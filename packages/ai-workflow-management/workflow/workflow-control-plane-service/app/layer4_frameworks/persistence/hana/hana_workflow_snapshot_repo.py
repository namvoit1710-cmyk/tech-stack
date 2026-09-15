"""SAP HANA implementation of ``IWorkflowSnapshotRepository``.

Persists frozen workflow definitions (one row per snapshot capture) to
the ``AIW_WORKFLOW_SNAPSHOTS`` table; see migration
``033_create_aiw_workflow_snapshots.sql``. The ``DEFINITION_JSON``
column holds the dict produced by ``Workflow.to_snapshot_dict``.
"""

import json
from typing import Any

from app.layer1_domain.entities.workflow_snapshot import WorkflowSnapshot
from app.layer2_application.repositories.workflow_snapshot_repository_interface import (
    IWorkflowSnapshotRepository,
)


from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin


class HanaWorkflowSnapshotRepository(AsyncRepoMixin, IWorkflowSnapshotRepository):

    def __init__(self, db: Any) -> None:
        self._db = db
        self._table = "AIW_WORKFLOW_SNAPSHOTS"

    def save(self, snapshot: WorkflowSnapshot) -> None:
        # Snapshots are immutable — INSERT, not UPSERT. A duplicate id
        # would indicate caller error; let the DB raise so it surfaces.
        sql = (
            f"INSERT INTO {self._table} "
            f"(ID, WORKFLOW_ID, WORKFLOW_VERSION, DEFINITION_JSON, CREATED_AT) "
            f"VALUES (:p0, :p1, :p2, :p3, :p4)"
        )
        self._db.execute_write(
            sql,
            (
                snapshot.id,
                snapshot.workflow_id,
                snapshot.workflow_version,
                json.dumps(snapshot.definition, default=str),
                snapshot.created_at,
            ),
        )

    def get_by_id(self, snapshot_id: str) -> WorkflowSnapshot | None:
        rows = self._db.execute_query(
            f"SELECT * FROM {self._table} WHERE ID = :p0", (snapshot_id,),
        )
        if not rows:
            return None
        return self._row_to_entity(rows[0])

    def delete(self, snapshot_id: str) -> None:
        self._db.execute_write(
            f"DELETE FROM {self._table} WHERE ID = :p0", (snapshot_id,),
        )

    def _row_to_entity(self, row: dict) -> WorkflowSnapshot:
        raw = row.get("definition_json")
        if isinstance(raw, (dict, list)):
            definition = raw
        else:
            try:
                definition = json.loads(raw) if raw is not None else {}
            except (json.JSONDecodeError, TypeError):
                definition = {}
        return WorkflowSnapshot(
            id=row["id"],
            workflow_id=row.get("workflow_id", ""),
            workflow_version=row.get("workflow_version", ""),
            definition=definition,
            created_at=row.get("created_at", ""),
        )
