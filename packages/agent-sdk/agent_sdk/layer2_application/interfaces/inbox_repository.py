from typing import Protocol

from agent_sdk.layer1_domain.entities.inbox_record import InboxRecord


class IInboxRepository(Protocol):
    def find_by_message_id(self, message_id: str) -> InboxRecord | None: ...

    def save(self, record: InboxRecord) -> InboxRecord: ...

    def update_status(
        self,
        message_id: str,
        status: str,
        *,
        error: str = "",
        processed_at: str = "",
    ) -> bool: ...
