from typing import Any, List, Optional, Protocol

from agent_sdk.layer1_domain.entities.agent_shared_state import (
    AuditLogEntry,
    Conversation,
    GlobalContext,
    Metadata,
    Task,
)
from agent_sdk.layer1_domain.entities.conversation_context import TaskHistory


class IAgentSharedStateRepository(Protocol):
    """Port for persisting and retrieving agent shared state."""

    # ── Full Conversation CRUD ──

    def save_conversation(self, conversation: Conversation) -> None: ...

    def get_conversation(self, conv_id: str) -> Optional[Conversation]: ...

    def list_conversation(
        self, user_id: str, skip: int = 0, top: int = 10
    ) -> list[Conversation]: ...

    def count_conversation(self, user_id: str) -> int: ...

    def get_conversation_by(
        self,
        conv_id: str,
        *,
        task_id: Optional[str] = None,
    ) -> Optional[Conversation]: ...

    def delete_conversation(self, conv_id: str) -> bool: ...

    # ── Granular section updates (only touches the target column) ──

    def get_metadata(self, conv_id: str) -> Optional[Metadata]: ...

    def update_metadata(self, conv_id: str, metadata: Metadata) -> None: ...

    def update_context(self, conv_id: str, context: GlobalContext) -> None: ...

    def upsert_executions(self, conv_id: str, executions: List[Task]) -> None: ...

    def update_task(
        self, conv_id: str, task_id: str, **kwargs: Any
    ) -> Optional[Task]: ...

    def get_task(self, conv_id: str, task_id: str) -> Optional[Task]: ...

    # ── Audit log (append-only) ──

    def append_audit_log(self, entry: AuditLogEntry) -> None: ...

    def get_audit_logs(
        self, conv_id: str, *, limit: int = 100
    ) -> List[AuditLogEntry]: ...

    def upsert_task_histories(
        self,
        conv_id: str,
        message_id: str,
        tasks: list[TaskHistory],
    ) -> list[TaskHistory]: ...
