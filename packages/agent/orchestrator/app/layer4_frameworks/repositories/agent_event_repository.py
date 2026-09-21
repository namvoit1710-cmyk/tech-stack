"""SAP HANA implementation of agent event persistence."""
from __future__ import annotations
from datetime import datetime
from dataclasses import asdict
from typing import Any

from app.layer1_domain.entities.orchestrator.agent_event import AgentEvent
from app.layer1_domain.entities.orchestrator.ui_event import UIEvent, UIEventRole, UIEventType
from app.layer2_application.interfaces.agent_event_repository import ReopenConversationRow
from agent_sdk.layer4_frameworks.persistence.hana.base_repository import BaseHanaRepository, IDatabaseConnection


class HanaAgentEventRepository(BaseHanaRepository):

    def __init__(self, db: IDatabaseConnection):
        super().__init__(db, "AIW_AGENT_EVENT")

    def save_event(self, event: UIEvent) -> None:
        columns = [
            "EVENT_ID",
            "ROLE",
            "EVENT_TYPE",
            "TIMESTAMP",
            "CORRELATION_ID",
            "CONV_ID",
            "METADATA",
            "PAYLOAD",
        ]
        values = (
            event.event_id,
            self._role_value(event.role),
            self._event_type_value(event.event_type),
            event.timestamp.isoformat(),
            event.correlation_id,
            event.conv_id,
            self._to_json(asdict(event.metadata) if event.metadata is not None else None),
            self._to_json(asdict(event.payload) if event.payload is not None else None),
        )
        self._insert(columns, values)

    def upsert_timeline_event(self, event: UIEvent) -> None:
        if self._should_skip_timeline_event(event):
            return

        payload_id = self._payload_id(event)
        sql = (
            "MERGE INTO AIW_CHAT_TIMELINE_NODE AS target "
            "USING (SELECT :p0 AS CONV_ID, :p1 AS PAYLOAD_ID, :p2 AS LATEST_EVENT_ID FROM DUMMY) AS src "
            "ON target.CONV_ID = src.CONV_ID AND target.PAYLOAD_ID = src.PAYLOAD_ID "
            "WHEN MATCHED THEN UPDATE SET "
            "target.LATEST_EVENT_ID = src.LATEST_EVENT_ID, "
            "target.UPDATED_AT = CURRENT_TIMESTAMP "
            "WHEN NOT MATCHED THEN INSERT (CONV_ID, PAYLOAD_ID, LATEST_EVENT_ID, CREATED_AT, UPDATED_AT) "
            "VALUES (src.CONV_ID, src.PAYLOAD_ID, src.LATEST_EVENT_ID, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        self._execute_write(sql, (event.conv_id, payload_id, event.event_id))

    def get_event_by_id(self, event_id: str) -> AgentEvent | None:
        sql = (
            f"SELECT EVENT_ID, ROLE, EVENT_TYPE, TIMESTAMP, CORRELATION_ID, CONV_ID, METADATA, PAYLOAD "
            f"FROM {self._table} "
            f"WHERE EVENT_ID = :p0"
        )
        rows = self._execute_query(sql, (event_id,))
        if not rows:
            return None
        return self._row_to_event(rows[0])

    def get_events_by_conversation_id(self, conversation_id: str) -> list[AgentEvent]:
        sql = (
            f"SELECT EVENT_ID, ROLE, EVENT_TYPE, TIMESTAMP, CORRELATION_ID, CONV_ID, METADATA, PAYLOAD "
            f"FROM {self._table} "
            f"WHERE CONV_ID = :p0 "
            f"ORDER BY TIMESTAMP ASC, EVENT_ID ASC"
        )
        rows = self._execute_query(sql, (conversation_id,))
        return [self._row_to_event(row) for row in rows]

    def list_latest_conversation_events(self, skip: int = 0, top: int = 10) -> list[AgentEvent]:
        sql = (
            f"SELECT event_row.EVENT_ID, event_row.ROLE, event_row.EVENT_TYPE, event_row.TIMESTAMP, "
            f"event_row.CORRELATION_ID, event_row.CONV_ID, event_row.METADATA, event_row.PAYLOAD "
            f"FROM {self._table} AS event_row "
            f"INNER JOIN ("
            f"    SELECT CONV_ID, MAX(TIMESTAMP) AS MAX_TIMESTAMP "
            f"    FROM {self._table} "
            f"    GROUP BY CONV_ID "
            f") AS latest_event "
            f"ON event_row.CONV_ID = latest_event.CONV_ID "
            f"AND event_row.TIMESTAMP = latest_event.MAX_TIMESTAMP "
            f"ORDER BY event_row.TIMESTAMP DESC, event_row.EVENT_ID DESC "
            f"LIMIT :p0 OFFSET :p1"
        )
        rows = self._execute_query(sql, (top, skip))
        return [self._row_to_event(row) for row in rows]

    def get_reopen_conversation_rows(self, conversation_id: str) -> list[ReopenConversationRow]:
        sql = (
            "SELECT "
            "timeline_row.CONV_ID AS CONV_ID, "
            "timeline_row.PAYLOAD_ID AS PAYLOAD_ID, "
            "timeline_row.CREATED_AT AS NODE_CREATED_AT, "
            "timeline_row.UPDATED_AT AS NODE_UPDATED_AT, "
            "event_row.EVENT_ID AS EVENT_ID, "
            "event_row.ROLE AS ROLE, "
            "event_row.EVENT_TYPE AS EVENT_TYPE, "
            "event_row.TIMESTAMP AS TIMESTAMP, "
            "event_row.CORRELATION_ID AS CORRELATION_ID, "
            "event_row.METADATA AS METADATA, "
            "event_row.PAYLOAD AS PAYLOAD "
            "FROM AIW_CHAT_TIMELINE_NODE AS timeline_row "
            "INNER JOIN AIW_AGENT_EVENT AS event_row "
            "ON event_row.EVENT_ID = timeline_row.LATEST_EVENT_ID "
            "WHERE timeline_row.CONV_ID = :p0 "
            "ORDER BY timeline_row.CREATED_AT ASC, timeline_row.PAYLOAD_ID ASC"
        )
        rows = self._execute_query(sql, (conversation_id,))
        return [self._row_to_reopen_row(row) for row in rows]

    def get_last_chat_toggle_event(self, conversation_id: str) -> ReopenConversationRow | None:
        sql = (
            "SELECT "
            "event_row.CONV_ID AS CONV_ID, "
            "event_row.EVENT_ID AS PAYLOAD_ID, "
            "event_row.TIMESTAMP AS NODE_CREATED_AT, "
            "event_row.TIMESTAMP AS NODE_UPDATED_AT, "
            "event_row.EVENT_ID AS EVENT_ID, "
            "event_row.ROLE AS ROLE, "
            "event_row.EVENT_TYPE AS EVENT_TYPE, "
            "event_row.TIMESTAMP AS TIMESTAMP, "
            "event_row.CORRELATION_ID AS CORRELATION_ID, "
            "event_row.METADATA AS METADATA, "
            "event_row.PAYLOAD AS PAYLOAD "
            "FROM AIW_AGENT_EVENT AS event_row "
            "WHERE event_row.CONV_ID = :p0 "
            "AND event_row.EVENT_TYPE IN ('chat:disable', 'chat:enable') "
            "ORDER BY event_row.TIMESTAMP DESC, event_row.EVENT_ID DESC "
            "LIMIT 1"
        )
        rows = self._execute_query(sql, (conversation_id,))
        if not rows:
            return None
        return self._row_to_reopen_row(rows[0])

    def _row_to_event(self, row: dict) -> AgentEvent:
        metadata = self._to_dict(self._from_json(row.get("metadata", row.get("METADATA"))))
        payload = self._to_dict(self._from_json(row.get("payload", row.get("PAYLOAD"))))
        raw_event_type = row.get("event_type", row.get("EVENT_TYPE")) or ""
        timestamp = row.get("timestamp", row.get("TIMESTAMP"))
        return AgentEvent(
            event_id=row.get("event_id", row.get("EVENT_ID", "")),
            role=self._role_from_db(row.get("role", row.get("ROLE", ""))),
            event_type=raw_event_type,
            timestamp=self._to_datetime(timestamp),
            correlation_id=row.get("correlation_id", row.get("CORRELATION_ID", "")),
            conv_id=row.get("conv_id", row.get("CONV_ID", "")),
            metadata=metadata or {},
            payload=payload or {},
        )

    def _row_to_reopen_row(self, row: dict[str, Any]) -> ReopenConversationRow:
        metadata = self._to_dict(self._from_json(row.get("metadata", row.get("METADATA"))))
        payload = self._to_dict(self._from_json(row.get("payload", row.get("PAYLOAD"))))
        return ReopenConversationRow(
            conv_id=row.get("conv_id", row.get("CONV_ID", "")),
            payload_id=row.get("payload_id", row.get("PAYLOAD_ID", "")),
            node_created_at=self._to_datetime(row.get("node_created_at", row.get("NODE_CREATED_AT"))),
            node_updated_at=self._to_datetime(row.get("node_updated_at", row.get("NODE_UPDATED_AT"))),
            event_id=row.get("event_id", row.get("EVENT_ID", "")),
            role=self._role_from_db(row.get("role", row.get("ROLE", ""))),
            event_type=self._event_type_from_db(row.get("event_type", row.get("EVENT_TYPE", ""))),
            timestamp=self._to_datetime(row.get("timestamp", row.get("TIMESTAMP"))),
            correlation_id=row.get("correlation_id", row.get("CORRELATION_ID", "")),
            metadata=metadata or {},
            payload=payload or {},
        )

    @staticmethod
    def _to_datetime(value) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and value:
            return datetime.fromisoformat(value)
        return datetime.min

    @staticmethod
    def _to_dict(value) -> dict:
        if value in (None, "null"):
            return {}
        if isinstance(value, dict):
            return value
        return {"value": value}

    @staticmethod
    def _event_type_value(value: Any) -> str:
        return value.value if hasattr(value, "value") else str(value)

    @staticmethod
    def _role_value(value: Any) -> str:
        return value.value if hasattr(value, "value") else str(value)

    @staticmethod
    def _role_from_db(value: Any) -> UIEventRole:
        if isinstance(value, UIEventRole):
            return value
        try:
            return UIEventRole(str(value or UIEventRole.ASSISTANT.value))
        except ValueError:
            return UIEventRole.ASSISTANT
        
    @staticmethod
    def _event_type_from_db(value: Any) -> UIEventType:
        if isinstance(value, UIEventType):
            return value

        if not isinstance(value, str) or not value:
            raise ValueError("event_type is required")

        return UIEventType(value)

    @staticmethod
    def _payload_id(event: UIEvent) -> str:
        payload = event.payload
        payload_id = getattr(payload, "id", "") if payload is not None else ""
        if not isinstance(payload_id, str) or not payload_id:
            raise ValueError("payload.id is required for timeline projection")
        return payload_id

    @classmethod
    def _should_skip_timeline_event(cls, event: UIEvent) -> bool:
        event_type = cls._event_type_value(event.event_type)
        if event_type == "chat:thinking":
            return True
        return event.payload is None
    
    def _insert(self, columns: list[str], values: tuple) -> None:
        insert_cols = ", ".join(columns)
        insert_vals = ", ".join(f":p{i}" for i, _ in enumerate(columns))
        sql = (
            f"INSERT INTO {self._table} ({insert_cols}) "
            f"VALUES ({insert_vals})"
        )
        self._execute_write(sql, values)
        
