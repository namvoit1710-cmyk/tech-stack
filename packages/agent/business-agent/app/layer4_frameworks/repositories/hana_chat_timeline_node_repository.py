from typing import Any

from agent_sdk.layer4_frameworks.persistence.hana.base_repository import BaseHanaRepository, IDatabaseConnection

from app.layer1_domain.entities.ui_event import UIEvent, UIEventType


class HanaChatTimelineNodeRepository(BaseHanaRepository):
    def __init__(self, db: IDatabaseConnection):
        super().__init__(db, "AIW_CHAT_TIMELINE_NODE")

    def upsert_from_event(self, event: UIEvent) -> None:
        if self._should_skip(event):
            return

        payload_id = self._payload_id(event)
        sql = (
            f"MERGE INTO {self._table} AS target "
            "USING (SELECT :p0 AS CONV_ID, :p1 AS PAYLOAD_ID, :p2 AS LATEST_EVENT_ID FROM DUMMY) AS src "
            "ON target.CONV_ID = src.CONV_ID AND target.PAYLOAD_ID = src.PAYLOAD_ID "
            "WHEN MATCHED THEN UPDATE SET "
            "target.LATEST_EVENT_ID = src.LATEST_EVENT_ID, "
            "target.UPDATED_AT = CURRENT_TIMESTAMP "
            "WHEN NOT MATCHED THEN INSERT (CONV_ID, PAYLOAD_ID, LATEST_EVENT_ID, CREATED_AT, UPDATED_AT) "
            "VALUES (src.CONV_ID, src.PAYLOAD_ID, src.LATEST_EVENT_ID, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        self._execute_write(sql, (event.conv_id, payload_id, event.event_id))

    @staticmethod
    def _payload_id(event: UIEvent) -> str:
        payload = event.payload
        payload_id = getattr(payload, "id", "") if payload is not None else ""
        if not isinstance(payload_id, str) or not payload_id:
            raise ValueError("payload.id is required for timeline projection")
        return payload_id

    @staticmethod
    def _event_type_value(value: Any) -> str:
        return value.value if hasattr(value, "value") else str(value)

    @classmethod
    def _should_skip(cls, event: UIEvent) -> bool:
        if event.payload is None:
            return True
        return cls._event_type_value(event.event_type) == UIEventType.CHAT_THINKING.value
