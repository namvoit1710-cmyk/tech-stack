"""SAP HANA implementation of ITaskRepository.

Persists Task entities to AIW_TASKS table.
resolved_input serialized as JSON in INPUT_REFS NCLOB column.
"""
import json
from typing import Any

from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects.loop_scope import LoopScope
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin


class HanaTaskRepository(AsyncRepoMixin, ITaskRepository):

    def __init__(self, db: Any) -> None:
        self._db = db
        self._table = "AIW_TASKS"
        # Tri-state: None = unknown, True/False = whether the SCOPE_STACK column
        # exists (migration 050). Self-healing: if the migration hasn't been
        # applied yet, save() falls back to the pre-050 column set instead of
        # failing every task write. Flips to True the first time the column-
        # inclusive UPSERT succeeds, False on an "invalid column name" error.
        self._has_scope_stack_col: bool | None = None

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

    def save(self, task: Task) -> None:
        base_columns = [
            "ID", "RUN_ID", "NODE_ID", "STATUS",
            "INPUT_REFS", "OUTPUT_REF", "RETRY_COUNT",
            "STARTED_AT", "COMPLETED_AT", "ERROR", "WORKER_ID",
            "OUTPUT_METADATA",
            "IDEMPOTENCY_KEY", "CREATED_AT",
            "RUN_GENERATION",
        ]
        base_values = (
            task.id,
            task.run_id,
            task.node_id,
            task.status.value if isinstance(task.status, TaskStatus) else task.status,
            self._to_json(task.resolved_input),
            task.output_ref,
            task.retry_count,
            task.started_at,
            task.completed_at,
            task.error,
            task.worker_type,
            self._to_json(task.output_metadata),
            task.idempotency_key,
            task.created_at,
            task.run_generation,
        )
        # Loop scope_stack — event-driven LOOP advancement identifies a loop-body
        # completion via get_innermost_scope(); without persisting it a reloaded
        # body task loses its scope and the loop stalls. Written only when the
        # SCOPE_STACK column (migration 050) is present; falls back gracefully
        # otherwise so a not-yet-migrated DB keeps saving tasks.
        scope_value = self._to_json(
            [s.to_dict() for s in task.scope_stack] if task.scope_stack else None
        )
        if self._has_scope_stack_col is not False:
            try:
                self._upsert(base_columns + ["SCOPE_STACK"], base_values + (scope_value,))
                self._has_scope_stack_col = True
                return
            except Exception as exc:  # noqa: BLE001
                if "SCOPE_STACK" not in str(exc).upper():
                    raise
                self._has_scope_stack_col = False  # column absent → fall through
        self._upsert(base_columns, base_values)

    def get_by_id(self, id: str) -> Task | None:
        rows = self._execute_query(
            f"SELECT * FROM {self._table} WHERE ID = :p0", (id,)
        )
        if not rows:
            return None
        return self._row_to_entity(rows[0])

    def list_by_run(self, run_id: str) -> list[Task]:
        rows = self._execute_query(
            f"SELECT * FROM {self._table} WHERE RUN_ID = :p0", (run_id,)
        )
        return [self._row_to_entity(r) for r in rows]

    def latest_by_run_node(self, run_id: str, node_id: str) -> Task | None:
        """Newest task for one node of one run — one row, not the whole run.

        Backed by IDX_AIW_TASKS_RUN_NODE (migration 054).

        ``CREATED_AT`` is NVARCHAR(64) holding ISO-8601, so DESC string order
        IS chronological order — the same comparison the Python scan this
        replaces was doing on the same strings.

        The ``COALESCE`` is load-bearing, and it is deliberately COALESCE
        rather than ``NULLS LAST``. The column is nullable, and HANA sorts NULL
        as the largest value, so a bare ``ORDER BY CREATED_AT DESC`` would
        return a timestamp-less row ahead of every real one — while the scan
        being replaced wrote ``t.created_at or ""`` and treated such a row as
        the oldest. Something has to bridge that, and ``COALESCE(x, '')`` is
        the literal transcription of ``x or ""``, where ``NULLS LAST`` is a
        dialect feature whose behaviour would have to be taken on trust: no
        HANA is reachable from the test suite, so a syntax or semantic
        difference would first surface as an exception on every dispatch in
        production. COALESCE is plain SQL-92 and says exactly what the Python
        said.

        It costs nothing. IDX_AIW_TASKS_RUN_NODE does the narrowing, and the
        residual for one (run, node) is a handful of rows — one per visit of
        that node — so wrapping the sort key cannot turn an index seek into a
        scan of anything that matters.
        """
        rows = self._execute_query(
            f"SELECT * FROM {self._table} "
            f"WHERE RUN_ID = :p0 AND NODE_ID = :p1 "
            f"ORDER BY COALESCE(CREATED_AT, '') DESC "
            f"LIMIT 1",
            (run_id, node_id),
        )
        if not rows:
            return None
        return self._row_to_entity(rows[0])

    def update_status(self, id: str, status: str) -> None:
        self._execute_write(
            f"UPDATE {self._table} SET STATUS = :p0 WHERE ID = :p1",
            (status, id),
        )

    def delete_all(self) -> int:
        count_rows = self._execute_query(f"SELECT COUNT(*) AS cnt FROM {self._table}")
        count = count_rows[0]["cnt"] if count_rows else 0
        self._execute_write(f"DELETE FROM {self._table}")
        return count

    def _row_to_entity(self, row: dict) -> Task:
        return Task(
            id=row["id"],
            run_id=row.get("run_id", ""),
            node_id=row.get("node_id", ""),
            status=TaskStatus(row.get("status", "PENDING")),
            resolved_input=self._from_json(row.get("input_refs")),
            output_ref=row.get("output_ref", ""),
            retry_count=row.get("retry_count", 0),
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
            error=row.get("error"),
            worker_type=row.get("worker_id", ""),
            output_metadata=self._from_json(row.get("output_metadata")),
            idempotency_key=row.get("idempotency_key", ""),
            created_at=row.get("created_at", ""),
            run_generation=int(row.get("run_generation", 0) or 0),
            scope_stack=self._scope_stack_from_json(row.get("scope_stack")),
        )

    @staticmethod
    def _scope_stack_from_json(raw: Any) -> list[LoopScope] | None:
        """Reconstruct a task's loop scope_stack from its persisted JSON."""
        if not raw:
            return None
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        if not isinstance(data, list) or not data:
            return None
        return [LoopScope.from_dict(d) for d in data if isinstance(d, dict)]
