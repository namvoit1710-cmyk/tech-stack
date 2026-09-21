from typing import Any, List, Optional

from agent_sdk.layer1_domain.entities.agent_shared_state import GlobalContext, Task
from agent_sdk.layer4_frameworks.persistence.hana.hana_agent_shared_state_repository import (
    HanaAgentSharedStateRepository,
)


def _row_value(row: dict[str, Any], key: str, default: Any = None) -> Any:
    if key.lower() in row:
        return row.get(key.lower(), default)
    if key.upper() in row:
        return row.get(key.upper(), default)
    return row.get(key, default)


class HanaBusinessAgentSharedStateRepository(HanaAgentSharedStateRepository):
    def get_conversation_id_by_run_id(self, run_id: str) -> Optional[str]:
        rows = self._repo._execute_query(
            f'SELECT "CONV_ID", "METADATA" FROM {self._repo._table} WHERE "METADATA" IS NOT NULL'
        )

        for row in rows:
            metadata = self._repo._from_json(_row_value(row, "METADATA")) or {}
            if metadata.get("current_run_id") == run_id and metadata.get("main_conv_id") in (None, ""):
                return _row_value(row, "CONV_ID")

        return None
    
    def get_context_by_conv_id(self, conv_id: str) -> Optional[GlobalContext]:
        rows = self._repo._execute_query(
            f'SELECT "CONTEXT" FROM {self._repo._table} WHERE "CONV_ID" = :p0',
            (conv_id,),
        )

        if not rows:
            return None
        existing_raw = self._repo._from_json(rows[0].get("context")) or {}
        return GlobalContext(**existing_raw)
    
    def get_executions_by_conv_id(self, conv_id: str) -> List[Task]:
        rows = self._repo._execute_query(
            f'SELECT "EXECUTIONS" FROM {self._repo._table} WHERE "CONV_ID" = :p0',
            (conv_id,),
        )

        if not rows:
            return []
        existing_raw = self._repo._from_json(rows[0].get("executions")) or []
        return [Task(**task) for task in existing_raw]

    def get_executions_by_message_id(
        self, conv_id: str, message_id: str
    ) -> List[Task]:
        rows = self._repo._execute_query(
            f'SELECT "CONTEXT", "EXECUTIONS" FROM {self._repo._table} WHERE "CONV_ID" = :p0',
            (conv_id,),
        )

        if not rows:
            return []

        context_raw = self._repo._from_json(_row_value(rows[0], "CONTEXT")) or {}
        executions_raw = self._repo._from_json(_row_value(rows[0], "EXECUTIONS")) or []

        message = next(
            (
                candidate
                for candidate in context_raw.get("conversation_context", [])
                if candidate.get("message_id") == message_id
            ),
            None,
        )
        if message is None:
            return []

        message_task_ids = {
            task.get("task_id") for task in (message.get("tasks") or [])
        }
        return [
            Task(**task)
            for task in executions_raw
            if task.get("task_id") in message_task_ids
        ]
    

