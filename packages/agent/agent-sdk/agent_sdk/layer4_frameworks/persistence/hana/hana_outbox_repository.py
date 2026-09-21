from __future__ import annotations

import json
from typing import Any

import hdbcli.dbapi

from agent_sdk.layer1_domain.entities.outbox_record import OutboxRecord

_CREATE_OUTBOX_DDL = (
    'CREATE COLUMN TABLE "AIW_OUTBOX_MESSAGES" ('
    '"OUTBOX_ID" NVARCHAR(255) PRIMARY KEY, '
    '"TOPIC" NVARCHAR(255), '
    '"MESSAGE" NCLOB, '
    '"MSG_KEY" NVARCHAR(255), '
    '"STATUS" NVARCHAR(20), '
    '"CREATED_AT" NVARCHAR(50), '
    '"PUBLISHED_AT" NVARCHAR(50), '
    '"ATTEMPTS" INTEGER)'
)


class HanaOutboxRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def setup(self) -> None:
        conn = self._db._create_connection()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(_CREATE_OUTBOX_DDL)
            except Exception as exc:
                if isinstance(exc, hdbcli.dbapi.Error):
                    if getattr(exc, "errorcode", None) != 288:
                        raise
                else:
                    raise
            conn.commit()
        finally:
            conn.close()

    def save(self, record: OutboxRecord) -> OutboxRecord:
        self._db.execute_write(
            'INSERT INTO "AIW_OUTBOX_MESSAGES" '
            '("OUTBOX_ID", "TOPIC", "MESSAGE", "MSG_KEY", "STATUS", '
            '"CREATED_AT", "PUBLISHED_AT", "ATTEMPTS") '
            "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7)",
            (
                record.outbox_id,
                record.topic,
                json.dumps(record.message),
                record.key,
                record.status,
                record.created_at,
                record.published_at,
                record.attempts,
            ),
        )
        return record

    def mark_published(self, outbox_id: str, published_at: str) -> bool:
        updated_rows = self._db.execute_write(
            'UPDATE "AIW_OUTBOX_MESSAGES" '
            'SET "STATUS" = :p1, "PUBLISHED_AT" = :p2 '
            'WHERE "OUTBOX_ID" = :p0',
            (outbox_id, "PUBLISHED", published_at),
        )
        return bool(updated_rows)

    def mark_failed(self, outbox_id: str) -> bool:
        updated_rows = self._db.execute_write(
            'UPDATE "AIW_OUTBOX_MESSAGES" '
            'SET "STATUS" = :p1, "ATTEMPTS" = "ATTEMPTS" + 1 '
            'WHERE "OUTBOX_ID" = :p0',
            (outbox_id, "FAILED"),
        )
        return bool(updated_rows)

    def find_pending(self, *, limit: int = 50) -> list[OutboxRecord]:
        rows = self._db.execute_query(
            'SELECT "OUTBOX_ID", "TOPIC", "MESSAGE", "MSG_KEY", "STATUS", '
            '"CREATED_AT", "PUBLISHED_AT", "ATTEMPTS" '
            'FROM "AIW_OUTBOX_MESSAGES" '
            'WHERE "STATUS" IN (:p0, :p1) '
            'ORDER BY "CREATED_AT" ASC '
            "LIMIT :p2",
            ("PENDING", "FAILED", limit),
        )
        return [self._row_to_record(row) for row in (rows or [])]

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> OutboxRecord:
        message_data = row.get("message")
        if hasattr(message_data, "read"):
            message_data = message_data.read()
        return OutboxRecord(
            outbox_id=row.get("outbox_id", ""),
            topic=row.get("topic", ""),
            message=json.loads(message_data or "{}"),
            key=row.get("msg_key", "") or "",
            status=row.get("status", ""),
            created_at=row.get("created_at", "") or "",
            published_at=row.get("published_at", "") or "",
            attempts=int(row.get("attempts") or 0),
        )
