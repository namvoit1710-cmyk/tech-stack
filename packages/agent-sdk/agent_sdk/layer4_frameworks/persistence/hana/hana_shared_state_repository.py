import json
from datetime import datetime, timedelta, timezone
from typing import Any

import hdbcli.dbapi

from agent_sdk.layer1_domain.entities.shared_state import (
    SharedStateLockInfo,
    SharedStateRecord,
)

_CREATE_SHARED_STATES_DDL = (
    'CREATE COLUMN TABLE "AIW_SHARED_STATES" ('
    '"STATE_KEY" NVARCHAR(255) PRIMARY KEY, '
    '"STATE_DATA" NCLOB, '
    '"VERSION" INTEGER, '
    '"UPDATED_AT" NVARCHAR(50), '
    '"LOCK_OWNER" NVARCHAR(255), '
    '"LOCK_ACQUIRED_AT" NVARCHAR(50), '
    '"LOCK_EXPIRES_AT" NVARCHAR(50))'
)


class HanaSharedStateRepository:
    def __init__(self, db: Any):
        self._db = db

    def setup(self) -> None:
        conn = self._db._create_connection()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(_CREATE_SHARED_STATES_DDL)
            except Exception as exc:
                if isinstance(exc, hdbcli.dbapi.Error):
                    if getattr(exc, "errorcode", None) != 288:
                        raise
                else:
                    raise
            conn.commit()
        finally:
            conn.close()

    def load(self, key: str) -> SharedStateRecord | None:
        return self.get(key)

    def get(self, key: str) -> SharedStateRecord | None:
        rows = self._db.execute_query(
            'SELECT "STATE_KEY", "STATE_DATA", "VERSION", "UPDATED_AT", '
            '"LOCK_OWNER", "LOCK_ACQUIRED_AT", "LOCK_EXPIRES_AT" '
            'FROM "AIW_SHARED_STATES" WHERE "STATE_KEY" = :p0',
            (key,),
        )
        if not rows:
            return None
        return self._row_to_record(rows[0])

    def delete(self, key: str) -> bool:
        deleted_rows = self._db.execute_write(
            'DELETE FROM "AIW_SHARED_STATES" WHERE "STATE_KEY" = :p0',
            (key,),
        )
        return bool(deleted_rows)

    def save(self, record: SharedStateRecord) -> SharedStateRecord:
        lock_owner = record.lock.owner if record.lock is not None else None
        lock_acquired_at = record.lock.acquired_at if record.lock is not None else None
        lock_expires_at = record.lock.expires_at if record.lock is not None else None
        self._db.execute_write(
            'MERGE INTO "AIW_SHARED_STATES" AS target '
            'USING (SELECT :p0 AS "STATE_KEY", :p1 AS "STATE_DATA", :p2 AS "VERSION", '
            ':p3 AS "UPDATED_AT", :p4 AS "LOCK_OWNER", :p5 AS "LOCK_ACQUIRED_AT", '
            ':p6 AS "LOCK_EXPIRES_AT" FROM DUMMY) AS src '
            'ON target."STATE_KEY" = src."STATE_KEY" '
            "WHEN MATCHED THEN UPDATE SET "
            '"STATE_DATA" = src."STATE_DATA", "VERSION" = src."VERSION", '
            '"UPDATED_AT" = src."UPDATED_AT", "LOCK_OWNER" = src."LOCK_OWNER", '
            '"LOCK_ACQUIRED_AT" = src."LOCK_ACQUIRED_AT", "LOCK_EXPIRES_AT" = src."LOCK_EXPIRES_AT" '
            "WHEN NOT MATCHED THEN INSERT "
            '("STATE_KEY", "STATE_DATA", "VERSION", "UPDATED_AT", "LOCK_OWNER", "LOCK_ACQUIRED_AT", "LOCK_EXPIRES_AT") '
            'VALUES (src."STATE_KEY", src."STATE_DATA", src."VERSION", src."UPDATED_AT", src."LOCK_OWNER", src."LOCK_ACQUIRED_AT", src."LOCK_EXPIRES_AT")',
            (
                record.key,
                json.dumps(record.state),
                record.version,
                record.updated_at,
                lock_owner,
                lock_acquired_at,
                lock_expires_at,
            ),
        )
        return record

    def compare_and_set(
        self,
        key: str,
        state: dict[str, Any],
        expected_version: int,
    ) -> SharedStateRecord | None:
        updated_at = datetime.now(timezone.utc).isoformat()
        next_version = expected_version + 1
        updated_rows = self._db.execute_write(
            'UPDATE "AIW_SHARED_STATES" AS target '
            'SET "STATE_DATA" = :p2, "VERSION" = :p3, "UPDATED_AT" = :p4 '
            'FROM (SELECT :p0 AS "STATE_KEY", :p1 AS "EXPECTED_VERSION" FROM DUMMY) AS src '
            'WHERE target."STATE_KEY" = src."STATE_KEY" '
            'AND target."VERSION" = src."EXPECTED_VERSION"',
            (key, expected_version, json.dumps(state), next_version, updated_at),
        )
        if not updated_rows:
            return None
        return SharedStateRecord(
            key=key,
            state=state,
            version=next_version,
            updated_at=updated_at,
        )

    def acquire_lock(
        self,
        key: str,
        owner: str,
        ttl_seconds: int,
    ) -> SharedStateRecord | None:
        acquired_at = datetime.now(timezone.utc)
        expires_at = acquired_at + timedelta(seconds=ttl_seconds)
        acquired_at_text = acquired_at.isoformat()
        expires_at_text = expires_at.isoformat()
        updated_rows = self._db.execute_write(
            'MERGE INTO "AIW_SHARED_STATES" AS target '
            'USING (SELECT :p0 AS "STATE_KEY", :p1 AS "LOCK_OWNER", :p2 AS "LOCK_ACQUIRED_AT", '
            ':p3 AS "LOCK_EXPIRES_AT", :p4 AS "UPDATED_AT" FROM DUMMY) AS src '
            'ON target."STATE_KEY" = src."STATE_KEY" '
            'WHEN MATCHED AND (target."LOCK_OWNER" IS NULL OR target."LOCK_OWNER" = src."LOCK_OWNER" OR target."LOCK_EXPIRES_AT" < src."UPDATED_AT") THEN UPDATE SET '
            '"LOCK_OWNER" = src."LOCK_OWNER", "LOCK_ACQUIRED_AT" = src."LOCK_ACQUIRED_AT", '
            '"LOCK_EXPIRES_AT" = src."LOCK_EXPIRES_AT", "UPDATED_AT" = src."UPDATED_AT" '
            "WHEN NOT MATCHED THEN INSERT "
            '("STATE_KEY", "STATE_DATA", "VERSION", "UPDATED_AT", "LOCK_OWNER", "LOCK_ACQUIRED_AT", "LOCK_EXPIRES_AT") '
            'VALUES (src."STATE_KEY", \'{}\', 0, src."UPDATED_AT", src."LOCK_OWNER", src."LOCK_ACQUIRED_AT", src."LOCK_EXPIRES_AT")',
            (key, owner, acquired_at_text, expires_at_text, acquired_at_text),
        )
        if not updated_rows:
            return None
        rows = self._db.execute_query(
            'SELECT "STATE_KEY", "STATE_DATA", "VERSION", "UPDATED_AT", '
            '"LOCK_OWNER", "LOCK_ACQUIRED_AT", "LOCK_EXPIRES_AT" '
            'FROM "AIW_SHARED_STATES" WHERE "STATE_KEY" = :p0',
            (key,),
        )
        if isinstance(rows, (list, tuple)) and rows:
            return self._row_to_record(rows[0])
        return SharedStateRecord(
            key=key,
            state={},
            version=0,
            updated_at=acquired_at_text,
            lock=SharedStateLockInfo(
                owner=owner,
                acquired_at=acquired_at_text,
                expires_at=expires_at_text,
            ),
        )

    def release_lock(self, key: str, owner: str) -> bool:
        updated_rows = self._db.execute_write(
            'UPDATE "AIW_SHARED_STATES" '
            'SET "LOCK_OWNER" = NULL, "LOCK_ACQUIRED_AT" = NULL, "LOCK_EXPIRES_AT" = NULL '
            'WHERE "STATE_KEY" = :p0 AND "LOCK_OWNER" = :p1',
            (key, owner),
        )
        return bool(updated_rows)

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> SharedStateRecord:
        lock = None
        if row.get("lock_owner"):
            lock = SharedStateLockInfo(
                owner=row["lock_owner"],
                acquired_at=row.get("lock_acquired_at") or "",
                expires_at=row.get("lock_expires_at") or "",
            )
        state_data = row.get("state_data")
        if hasattr(state_data, "read"):
            state_data = state_data.read()
        return SharedStateRecord(
            key=row.get("state_key", ""),
            state=json.loads(state_data or "{}"),
            version=int(row.get("version") or 0),
            updated_at=row.get("updated_at") or "",
            lock=lock,
        )
