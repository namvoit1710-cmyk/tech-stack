from dataclasses import asdict
from datetime import datetime
from enum import Enum
from typing import Any

from agent_sdk.layer4_frameworks.persistence.hana.base_repository import BaseHanaRepository, IDatabaseConnection

from app.layer1_domain.entities.ui_event import UIEvent


class HanaUIEventRepository(BaseHanaRepository):
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
            event.role,
            self._event_type_value(event.event_type),
            event.timestamp.isoformat(),
            event.correlation_id,
            event.conv_id,
            self._to_json(self._json_safe(asdict(event.metadata)) if event.metadata is not None else None),
            self._to_json(self._json_safe(asdict(event.payload)) if event.payload is not None else None),
        )
        self._insert(columns, values)

    def _insert(self, columns: list[str], values: tuple) -> None:
        insert_cols = ", ".join(columns)
        insert_vals = ", ".join(f":p{i}" for i, _ in enumerate(columns))
        sql = (
            f"INSERT INTO {self._table} ({insert_cols}) "
            f"VALUES ({insert_vals})"
        )
        self._execute_write(sql, values)

    @staticmethod
    def _event_type_value(value: Any) -> str:
        return value.value if hasattr(value, "value") else str(value)

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime) or hasattr(value, "isoformat"):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._json_safe(item) for item in value]
        return value
