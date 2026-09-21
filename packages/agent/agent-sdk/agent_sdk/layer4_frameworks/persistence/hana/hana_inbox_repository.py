from __future__ import annotations

import json
from typing import Any

import hdbcli.dbapi

from agent_sdk.layer1_domain.entities.inbox_record import InboxRecord

_CREATE_INBOX_DDL = (
    'CREATE COLUMN TABLE "AIW_INBOX_MESSAGES" ('
    '"MESSAGE_ID" NVARCHAR(255) PRIMARY KEY, '
    '"IDEMPOTENCY_KEY" NVARCHAR(255), '
    '"PAYLOAD" NCLOB, '
    '"STATUS" NVARCHAR(20), '
    '"RECEIVED_AT" NVARCHAR(50), '
    '"PROCESSED_AT" NVARCHAR(50), '
    '"ERROR" NCLOB)'
)


class HanaInboxRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def setup(self) -> None:
        conn = self._db._create_connection()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(_CREATE_INBOX_DDL)
            except Exception as exc:
                if isinstance(exc, hdbcli.dbapi.Error):
                    if getattr(exc, "errorcode", None) != 288:
                        raise
                else:
                    raise
            conn.commit()
        finally:
            conn.close()

    def find_by_message_id(self, message_id: str) -> InboxRecord | None:
        rows = self._db.execute_query(
            'SELECT "MESSAGE_ID", "IDEMPOTENCY_KEY", "PAYLOAD", "STATUS", '
            '"RECEIVED_AT", "PROCESSED_AT", "ERROR" '
            'FROM "AIW_INBOX_MESSAGES" WHERE "MESSAGE_ID" = :p0',
            (message_id,),
        )
        if not rows:
            return None
        return self._row_to_record(rows[0])

    def save(self, record: InboxRecord) -> InboxRecord:
        self._db.execute_write(
            'MERGE INTO "AIW_INBOX_MESSAGES" AS target '
            'USING (SELECT :p0 AS "MESSAGE_ID", :p1 AS "IDEMPOTENCY_KEY", '
            ':p2 AS "PAYLOAD", :p3 AS "STATUS", :p4 AS "RECEIVED_AT", '
            ':p5 AS "PROCESSED_AT", :p6 AS "ERROR" FROM DUMMY) AS src '
            'ON target."MESSAGE_ID" = src."MESSAGE_ID" '
            "WHEN MATCHED THEN UPDATE SET "
            '"IDEMPOTENCY_KEY" = src."IDEMPOTENCY_KEY", '
            '"PAYLOAD" = src."PAYLOAD", '
            '"STATUS" = src."STATUS", '
            '"RECEIVED_AT" = src."RECEIVED_AT", '
            '"PROCESSED_AT" = src."PROCESSED_AT", '
            '"ERROR" = src."ERROR" '
            "WHEN NOT MATCHED THEN INSERT "
            '("MESSAGE_ID", "IDEMPOTENCY_KEY", "PAYLOAD", "STATUS", '
            '"RECEIVED_AT", "PROCESSED_AT", "ERROR") '
            'VALUES (src."MESSAGE_ID", src."IDEMPOTENCY_KEY", src."PAYLOAD", '
            'src."STATUS", src."RECEIVED_AT", src."PROCESSED_AT", src."ERROR")',
            (
                record.message_id,
                record.idempotency_key,
                json.dumps(record.payload),
                record.status,
                record.received_at,
                record.processed_at,
                record.error,
            ),
        )
        return record

    def update_status(
        self,
        message_id: str,
        status: str,
        *,
        error: str = "",
        processed_at: str = "",
    ) -> bool:
        updated_rows = self._db.execute_write(
            'UPDATE "AIW_INBOX_MESSAGES" '
            'SET "STATUS" = :p1, "ERROR" = :p2, "PROCESSED_AT" = :p3 '
            'WHERE "MESSAGE_ID" = :p0',
            (message_id, status, error, processed_at),
        )
        return bool(updated_rows)

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> InboxRecord:
        payload_data = row.get("payload")
        if hasattr(payload_data, "read"):
            payload_data = payload_data.read()
        error_data = row.get("error")
        if hasattr(error_data, "read"):
            error_data = error_data.read()
        return InboxRecord(
            message_id=row.get("message_id", ""),
            idempotency_key=row.get("idempotency_key", ""),
            payload=json.loads(payload_data or "{}"),
            status=row.get("status", ""),
            received_at=row.get("received_at", ""),
            processed_at=row.get("processed_at", "") or "",
            error=error_data or "",
        )
