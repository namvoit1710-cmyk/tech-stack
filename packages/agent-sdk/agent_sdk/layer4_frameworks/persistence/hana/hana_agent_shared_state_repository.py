"""HANA persistence for agent shared state.

Operates on two tables:
  - ``AIW_AGENT_STATES``      – one row per conversation (MERGE / upsert)
  - ``AIW_AGENT_AUDIT_LOGS``  – append-only action history
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel

from agent_sdk.layer1_domain.entities.agent_shared_state import (
    AgentSharedState,
    AuditLogEntry,
    Conversation,
    GlobalContext,
    History,
    Metadata,
    Task,
)
from agent_sdk.layer1_domain.entities.conversation_context import TaskHistory
from agent_sdk.layer2_application.interfaces.agent_shared_state_repository import (
    IAgentSharedStateRepository,
)
from agent_sdk.layer4_frameworks.persistence.hana.base_repository import (
    BaseHanaRepository,
)
from agent_sdk.layer4_frameworks.persistence.hana.migrations import (
    migrate as _run_migrations,
)

logger = logging.getLogger(__name__)

_STATES_TABLE = '"AIW_AGENT_STATES"'
_AUDIT_TABLE = '"AIW_AGENT_AUDIT_LOGS"'

_STATES_COLUMNS = [
    '"CONV_ID"',
    '"USER_ID"',
    '"METADATA"',
    '"CONTEXT"',
    '"EXECUTIONS"',
    '"UPDATED_AT"',
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_object_model(obj: Any) -> str:
    """Serialize a Pydantic model or list of models to JSON string."""
    if obj is None:
        return "null"
    if isinstance(obj, BaseModel):
        return json.dumps(obj.model_dump(by_alias=True))
    if isinstance(obj, dict):
        return json.dumps(obj)
    return "null"


def _serialize_list_object_model(obj: list) -> str:
    """Serialize a Pydantic model or list of models to JSON string."""
    if obj is None or not isinstance(obj, list):
        return "null"

    return json.dumps(
        [
            item.model_dump(by_alias=True) if hasattr(item, "model_dump") else item
            for item in obj
        ]
    )


def _row_value(row: dict[str, Any], key: str, default: Any = None) -> Any:
    if key.lower() in row:
        return row.get(key.lower(), default)
    if key.upper() in row:
        return row.get(key.upper(), default)
    return row.get(key, default)


class HanaAgentSharedStateRepository(IAgentSharedStateRepository):
    """Implements ``IAgentSharedStateRepository`` against SAP HANA."""

    def __init__(self, repo: BaseHanaRepository) -> None:
        self._repo = repo

    # ── Setup / Migration ──

    def setup(self) -> None:
        _run_migrations(self._repo._db)

    # ── Full Conversation CRUD ──

    def save_conversation(self, conversation: Conversation) -> None:
        shared = conversation.shared_state
        values = (
            conversation.conv_id,
            conversation.user_id,
            _serialize_object_model(shared.metadata),
            _serialize_object_model(shared.context),
            _serialize_list_object_model(shared.executions),
            _now_iso(),
        )
        self._repo._upsert(
            [c.strip('"') for c in _STATES_COLUMNS],
            values,
            key_column="CONV_ID",
        )

    def get_conversation(self, conv_id: str) -> Optional[Conversation]:
        rows = self._repo._execute_query(
            f"SELECT {', '.join(_STATES_COLUMNS)} "
            f'FROM {_STATES_TABLE} WHERE "CONV_ID" = :p0',
            (conv_id,),
        )
        if not rows:
            return None

        row = rows[0]
        metadata_raw = self._repo._from_json(_row_value(row, "METADATA")) or {}
        context_raw = self._repo._from_json(_row_value(row, "CONTEXT")) or {}
        executions_raw = self._repo._from_json(_row_value(row, "EXECUTIONS")) or []

        shared_state = AgentSharedState(
            metadata=Metadata(**metadata_raw),
            context=GlobalContext(**context_raw),
            executions=[Task(**t) for t in executions_raw],
            histories=[],
        )

        return Conversation(
            conv_id=_row_value(row, "CONV_ID", ""),
            user_id=_row_value(row, "USER_ID", ""),
            shared_state=shared_state,
        )

    def get_conversation_by(
        self,
        conv_id: str,
        *,
        task_id: Optional[str] = None,
    ) -> Optional[Conversation]:
        """Return a filtered view of a conversation for Supervisor review.

        Always loads the full row by *conv_id*, then narrows the result:

        * **task_id** -- keeps the target task plus any sub-tasks that
          depend on it (direct ``depend_on`` reference), and audit logs
          whose ``action_name`` matches the task goals.

        When neither filter is supplied the method behaves identically to
        ``get_conversation``.
        """
        rows = self._repo._execute_query(
            f"SELECT {', '.join(_STATES_COLUMNS)} "
            f'FROM {_STATES_TABLE} WHERE "CONV_ID" = :p0',
            (conv_id,),
        )
        if not rows:
            return None

        row = rows[0]
        metadata_raw = self._repo._from_json(_row_value(row, "METADATA")) or {}
        context_raw = self._repo._from_json(_row_value(row, "CONTEXT")) or {}
        executions_raw = self._repo._from_json(_row_value(row, "EXECUTIONS")) or []

        all_tasks = [Task(**t) for t in executions_raw]

        # -- Filter by task_id --
        if task_id is not None:
            target_task = next((t for t in all_tasks if t.id == task_id), None)
            if target_task is not None:
                related_ids = {task_id}
                for t in all_tasks:
                    if task_id in t.depend_on:
                        related_ids.add(t.id)
                filtered_tasks = [t for t in all_tasks if t.id in related_ids]
                task_names = {t.goal for t in filtered_tasks}
                audit_entries = self._get_audit_logs_by_actions(conv_id, task_names)
            else:
                filtered_tasks = []
                audit_entries = []

        # -- No filter -- same as get_conversation --
        else:
            filtered_tasks = all_tasks
            audit_entries = []

        histories = [
            History(
                role="system",
                content=f"[{e.status}] {e.action_name}: {json.dumps(e.details) if e.details else 'OK'}",
                agent_id=e.agent_name,
                timestamp=e.created_at,
                conv_id=e.conv_id,
                sub_conv_id=e.checkpoint_id,
            )
            for e in audit_entries
        ]

        shared_state = AgentSharedState(
            metadata=Metadata(**metadata_raw),
            context=GlobalContext(**context_raw),
            executions=filtered_tasks,
            histories=histories,
        )

        return Conversation(
            conv_id=_row_value(row, "CONV_ID", ""),
            user_id=_row_value(row, "USER_ID", ""),
            shared_state=shared_state,
        )

    def delete_conversation(self, conv_id: str) -> bool:
        self._repo._execute_write(
            f'DELETE FROM {_STATES_TABLE} WHERE "CONV_ID" = :p0',
            (conv_id,),
        )
        return True

    def get_task(self, conv_id: str, task_id: str) -> Optional[Task]:
        """Return a single Task by *task_id* from the conversation's executions.

        Returns ``None`` if the conversation or task is not found.
        """
        rows = self._repo._execute_query(
            f'SELECT "EXECUTIONS" FROM {_STATES_TABLE} WHERE "CONV_ID" = :p0',
            (conv_id,),
        )
        if not rows:
            return None

        existing_raw = self._repo._from_json(rows[0].get("executions")) or []
        for t in existing_raw:
            task = Task(**t)
            if task.id == task_id:
                return task
        return None

    # ── Granular section updates ──
    def get_metadata(self, conv_id: str) -> Optional[Metadata]:
        rows = self._repo._execute_query(
            f'SELECT "METADATA" FROM {_STATES_TABLE} WHERE "CONV_ID" = :p0',
            (conv_id,),
        )
        if not rows:
            return None
        existing_raw = self._repo._from_json(rows[0].get("metadata")) or {}
        return Metadata(**existing_raw)

    def update_metadata(self, conv_id: str, metadata: Metadata) -> None:
        # SA-892 (task-scoped stop): the earlier "sticky CANCELLED" guard is
        # retired. Stop no longer writes Metadata.status=CANCELLED (it cancels the
        # current message's tasks instead), so there is no conversation-status to
        # protect from a stale turn-write. This is a plain full-blob metadata write.
        self._repo._execute_write(
            f"UPDATE {_STATES_TABLE} "
            f'SET "METADATA" = :p0, "UPDATED_AT" = :p1 '
            f'WHERE "CONV_ID" = :p2',
            (_serialize_object_model(metadata), _now_iso(), conv_id),
        )

    def update_context(self, conv_id: str, context: GlobalContext) -> None:
        self._repo._execute_write(
            f"UPDATE {_STATES_TABLE} "
            f'SET "CONTEXT" = :p0, "UPDATED_AT" = :p1 '
            f'WHERE "CONV_ID" = :p2',
            (_serialize_object_model(context), _now_iso(), conv_id),
        )

    def _try_insert_executions(self, conv_id: str, executions: list[Task]) -> bool:
        """
        TRY TO INSERT.
        Return True if success, False if error Duplicate Key.
        """
        new_updated_at = _now_iso()
        try:
            affected_rows = self._repo._execute_write(
                f'INSERT INTO {_STATES_TABLE} ("CONV_ID", "EXECUTIONS", "UPDATED_AT") '
                f"VALUES (:p0, :p1, :p2)",
                (conv_id, _serialize_list_object_model(executions), new_updated_at),
            )
            return (affected_rows or 0) > 0
        except Exception:
            return False

    def _try_merge_and_update_executions(
        self, conv_id: str, row: dict, executions: list[Task]
    ) -> bool:
        """
        MERGE old and new task, then UPDATE with Optimistic Locking.
        Return True if success, False if data changed by other.
        """
        current_executions = _row_value(row, "EXECUTIONS")
        current_updated_at = _row_value(row, "UPDATED_AT")

        # 1. Parse JSON
        existing_raw = (
            self._repo._from_json(current_executions) if current_executions else []
        )
        existing_tasks = [Task(**t) for t in existing_raw]

        # 2. Merge tasks
        existing_map = {t.id: t for t in existing_tasks}
        for task in executions:
            existing_map[task.id] = task
        merged = list(existing_map.values())

        # 3. Update with Optimistic Locking
        new_updated_at = _now_iso()
        affected_rows = self._repo._execute_write(
            f"UPDATE {_STATES_TABLE} "
            f'SET "EXECUTIONS" = :p0, "UPDATED_AT" = :p1 '
            f'WHERE "CONV_ID" = :p2 AND "UPDATED_AT" = :p3',
            (
                _serialize_list_object_model(merged),
                new_updated_at,
                conv_id,
                current_updated_at,
            ),
        )
        return (affected_rows or 0) > 0

    def upsert_executions(
        self, conv_id: str, executions: list[Task], max_retries: int = 3
    ) -> None:
        """Upsert tasks into the existing execution list.

        Loads the current executions from DB, then for each incoming task:
        - If a task with the same ``id`` already exists, it is replaced.
        - Otherwise the task is appended.
        """
        for attempt in range(max_retries):
            rows = self._repo._execute_query(
                f'SELECT "EXECUTIONS", "UPDATED_AT" FROM {_STATES_TABLE} WHERE "CONV_ID" = :p0',
                (conv_id,),
            )

            if not rows:
                if self._try_insert_executions(conv_id, executions):
                    return
            else:
                if self._try_merge_and_update_executions(conv_id, rows[0], executions):
                    return

            if attempt < max_retries - 1:
                time.sleep(0.1 * (attempt + 1))
                continue

        # Throw exception if max retries
        raise Exception(
            f"Failed to update executions for conv_id '{conv_id}' "
            f"after {max_retries} attempts due to high concurrency."
        )

    def _try_merge_and_update_task(
        self,
        conv_id: str,
        row: dict,
        task_id: str,
        field_to_alias: dict[str, str],
        kwargs: dict[str, Any],
    ) -> Optional[Task]:
        """Merge *kwargs* into the target task and write it back with an
        optimistic lock (CAS on ``UPDATED_AT``).

        Returns the updated ``Task`` on success, ``None`` on a CAS conflict (the
        row was changed concurrently — caller re-reads and retries). Raises
        ``ValueError`` if the task is not present (deterministic — retrying will
        not help).
        """
        current_executions = _row_value(row, "EXECUTIONS")
        current_updated_at = _row_value(row, "UPDATED_AT")

        existing_raw = (
            self._repo._from_json(current_executions) if current_executions else []
        )
        task_dicts = list(existing_raw or [])

        # Find target task by task_id (alias of id)
        target = None
        for t in task_dicts:
            if t.get("task_id") == task_id:
                target = t
                break
        if target is None:
            raise ValueError(f"Task '{task_id}' not found in conv '{conv_id}'")

        # Merge kwargs using alias keys for serialized JSON
        for field_name, value in kwargs.items():
            alias_key = field_to_alias[field_name]
            target[alias_key] = value

        new_updated_at = _now_iso()
        affected_rows = self._repo._execute_write(
            f"UPDATE {_STATES_TABLE} "
            f'SET "EXECUTIONS" = :p0, "UPDATED_AT" = :p1 '
            f'WHERE "CONV_ID" = :p2 AND "UPDATED_AT" = :p3',
            (json.dumps(task_dicts), new_updated_at, conv_id, current_updated_at),
        )
        if (affected_rows or 0) > 0:
            return Task.model_validate(target)
        return None

    def update_task(self, conv_id: str, task_id: str, **kwargs: Any) -> Optional[Task]:
        """Partial update of a single task's fields.

        Reads the EXECUTIONS column, finds the task by ``task_id``, merges only
        the supplied *kwargs* into its dict, then writes it back under an
        optimistic lock — the same compare-and-set-on-``UPDATED_AT`` + bounded
        retry pattern as ``upsert_executions`` (see
        ``_try_merge_and_update_executions``). This prevents a racing turn's
        blind read-merge-write from clobbering a concurrently-written field
        (e.g. a Stop's ``CANCELLED``): a CAS conflict re-reads and re-applies the
        merge instead of losing the other write.

        Raises ``ValueError`` if the task is not found.
        """
        # Build field-name → alias mapping from Task model
        _field_to_alias = {
            name: (f.alias if f.alias else name)
            for name, f in Task.model_fields.items()
        }
        valid_fields = set(_field_to_alias.keys())
        invalid = set(kwargs.keys()) - valid_fields
        if invalid:
            raise ValueError(f"Unknown Task fields: {invalid}")

        max_retries = 3
        for attempt in range(max_retries):
            rows = self._repo._execute_query(
                f'SELECT "EXECUTIONS", "UPDATED_AT" FROM {_STATES_TABLE} '
                f'WHERE "CONV_ID" = :p0',
                (conv_id,),
            )
            if not rows:
                raise ValueError(f"Task '{task_id}' not found in conv '{conv_id}'")

            updated = self._try_merge_and_update_task(
                conv_id, rows[0], task_id, _field_to_alias, kwargs
            )
            if updated is not None:
                return updated

            if attempt < max_retries - 1:
                time.sleep(0.1 * (attempt + 1))
                continue

        # CAS kept losing under concurrency.
        raise Exception(
            f"Failed to update task '{task_id}' for conv_id '{conv_id}' "
            f"after {max_retries} attempts due to high concurrency."
        )

    # ── Audit logs (append-only) ──

    def append_audit_log(self, entry: AuditLogEntry) -> None:
        details_json = json.dumps(entry.details) if entry.details is not None else None
        self._repo._execute_write(
            f"INSERT INTO {_AUDIT_TABLE} "
            f'("ID", "CONV_ID", "CHECKPOINT_ID", "AGENT_NAME", '
            f'"ACTION_NAME", "STATUS", "DETAILS", "CREATED_AT") '
            f"VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7)",
            (
                entry.id,
                entry.conv_id,
                entry.checkpoint_id,
                entry.agent_name,
                entry.action_name,
                entry.status,
                details_json,
                entry.created_at.isoformat(),
            ),
        )

    def get_audit_logs(self, conv_id: str, *, limit: int = 100) -> list[AuditLogEntry]:
        rows = self._repo._execute_query(
            f'SELECT "ID", "CONV_ID", "CHECKPOINT_ID", "AGENT_NAME", '
            f'"ACTION_NAME", "STATUS", "DETAILS", "CREATED_AT" '
            f"FROM {_AUDIT_TABLE} "
            f'WHERE "CONV_ID" = :p0 '
            f'ORDER BY "CREATED_AT" ASC '
            f"LIMIT :p1",
            (conv_id, limit),
        )
        return self._rows_to_audit_entries(rows)

    def _get_audit_logs_by_actions(
        self, conv_id: str, action_names: set[str], *, limit: int = 200
    ) -> list[AuditLogEntry]:
        if not action_names:
            return []
        placeholders = ", ".join(f":p{i + 1}" for i in range(len(action_names)))
        names_list = list(action_names)
        rows = self._repo._execute_query(
            f'SELECT "ID", "CONV_ID", "CHECKPOINT_ID", "AGENT_NAME", '
            f'"ACTION_NAME", "STATUS", "DETAILS", "CREATED_AT" '
            f"FROM {_AUDIT_TABLE} "
            f'WHERE "CONV_ID" = :p0 AND "ACTION_NAME" IN ({placeholders}) '
            f'ORDER BY "CREATED_AT" ASC '
            f"LIMIT :p{len(names_list) + 1}",
            (conv_id, *names_list, limit),
        )
        return self._rows_to_audit_entries(rows)

    def _rows_to_audit_entries(self, rows: list[dict]) -> list[AuditLogEntry]:
        result: list[AuditLogEntry] = []
        for row in rows:
            details = self._repo._from_json(_row_value(row, "DETAILS"))
            created_at = _row_value(row, "CREATED_AT")
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            result.append(
                AuditLogEntry(
                    id=_row_value(row, "ID", ""),
                    conv_id=_row_value(row, "CONV_ID", ""),
                    checkpoint_id=_row_value(row, "CHECKPOINT_ID", ""),
                    agent_name=_row_value(row, "AGENT_NAME", ""),
                    action_name=_row_value(row, "ACTION_NAME", ""),
                    status=_row_value(row, "STATUS", ""),
                    details=details if isinstance(details, dict) else None,
                    created_at=created_at,
                )
            )
        return result

    def list_conversation(
        self, user_id: str, skip: int = 0, top: int = 10
    ) -> list[Conversation]:
        data_sql = (
            f"SELECT {', '.join(_STATES_COLUMNS)} "
            f"FROM {_STATES_TABLE} "
            f'WHERE "USER_ID" = :p0 '
            f'ORDER BY "UPDATED_AT" DESC '
            f"LIMIT :p1 OFFSET :p2"
        )
        rows = self._repo._execute_query(data_sql, (user_id, top, skip))
        conversations: list[Conversation] = []
        for row in rows:
            metadata_raw = self._repo._from_json(_row_value(row, "METADATA")) or {}
            context_raw = self._repo._from_json(_row_value(row, "CONTEXT")) or {}
            executions_raw = self._repo._from_json(_row_value(row, "EXECUTIONS")) or []

            shared_state = AgentSharedState(
                metadata=Metadata(**metadata_raw),
                context=GlobalContext(**context_raw),
                executions=[Task(**task) for task in executions_raw],
                histories=[],
            )
            conversations.append(
                Conversation(
                    conv_id=_row_value(row, "CONV_ID", ""),
                    user_id=_row_value(row, "USER_ID", ""),
                    shared_state=shared_state,
                )
            )
        return conversations

    def count_conversation(self, user_id: str) -> int:
        data_sql = (
            f"SELECT count(CONV_ID) as cnt "
            f"FROM {_STATES_TABLE} "
            f'WHERE "USER_ID" = :p0'
        )
        rows = self._repo._execute_query(data_sql, (user_id,))
        return int(rows[0].get("cnt")) or 0

    def upsert_task_histories(
        self,
        conv_id: str,
        message_id: str,
        tasks: list[TaskHistory],
    ) -> list[TaskHistory]:
        rows = self._repo._execute_query(
            f'SELECT "CONTEXT" FROM {_STATES_TABLE} WHERE "CONV_ID" = :p0',
            (conv_id,),
        )

        context_dict = (
            self._repo._from_json(_row_value(rows[0], "CONTEXT")) if rows else {}
        )

        context = GlobalContext.model_validate(context_dict)

        # Find message
        message = next(
            (m for m in context.conversation_context if m.message_id == message_id),
            None,
        )

        if message is None:
            raise ValueError(f"Message '{message_id}' not found in conv '{conv_id}'")

        if message.tasks is None:
            message.tasks = []

        # Map existing tasks by task_id
        existing_task_indexes = {
            task.task_id: idx for idx, task in enumerate(message.tasks)
        }

        result: list[TaskHistory] = []

        for incoming_task in tasks:
            idx = existing_task_indexes.get(incoming_task.task_id)

            if idx is None:
                # Insert new task
                message.tasks.append(incoming_task)
                existing_task_indexes[incoming_task.task_id] = len(message.tasks) - 1
                result.append(incoming_task)
            else:
                # Replace existing task
                message.tasks[idx] = incoming_task
                result.append(incoming_task)

        self._repo._execute_write(
            f"""
            UPDATE {_STATES_TABLE}
            SET "CONTEXT" = :p0,
                "UPDATED_AT" = :p1
            WHERE "CONV_ID" = :p2
            """,
            (
                context.model_dump_json(),
                _now_iso(),
                conv_id,
            ),
        )

        return result
