"""Idempotent DDL migrations for agent shared-state HANA tables.

Tables:
  - AIW_AGENT_STATES      – hot data, one row per conversation (upsert)
  - AIW_AGENT_AUDIT_LOGS  – cold data, append-only action history

Each statement is executed individually.  HANA error code 288
("cannot use duplicate table name") and 289 ("duplicate index name")
are caught and silently skipped so the migration is re-runnable.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_ERR_DUPLICATE_TABLE = 288
_ERR_DUPLICATE_INDEX = 289

_DDL_STATEMENTS: list[str] = [
    # ── AIW_AGENT_STATES (hot – upsert by CONV_ID) ──
    """
    CREATE COLUMN TABLE "AIW_AGENT_STATES" (
        "CONV_ID"        NVARCHAR(50)   NOT NULL PRIMARY KEY,
        "USER_ID"        NVARCHAR(50)   NOT NULL,
        "METADATA"       NCLOB,
        "CONTEXT"        NCLOB,
        "EXECUTIONS"     NCLOB,
        "UPDATED_AT"     NVARCHAR(50)   NOT NULL
    )
    """,
    """CREATE INDEX "IDX_AIW_AGENT_STATES_USER" ON "AIW_AGENT_STATES" ("USER_ID")""",
    # ── AIW_AGENT_AUDIT_LOGS (cold – append-only) ──
    """
    CREATE COLUMN TABLE "AIW_AGENT_AUDIT_LOGS" (
        "ID"              NVARCHAR(36)   NOT NULL PRIMARY KEY,
        "CONV_ID"         NVARCHAR(50)   NOT NULL,
        "CHECKPOINT_ID"   NVARCHAR(50)   NOT NULL,
        "AGENT_NAME"      NVARCHAR(50)   NOT NULL,
        "ACTION_NAME"     NVARCHAR(100)  NOT NULL,
        "STATUS"          NVARCHAR(20)   NOT NULL,
        "DETAILS"         NCLOB,
        "CREATED_AT"      TIMESTAMP      DEFAULT CURRENT_UTCTIMESTAMP
    )
    """,
    """CREATE INDEX "IDX_AIW_AUDIT_LOGS_CONV" ON "AIW_AGENT_AUDIT_LOGS" ("CONV_ID")""",
    """CREATE INDEX "IDX_AIW_AUDIT_LOGS_AGENT" ON "AIW_AGENT_AUDIT_LOGS" ("AGENT_NAME")""",
]

_TABLE_NAMES = [
    "AIW_AGENT_STATES",
    "AIW_AGENT_AUDIT_LOGS",
]


def migrate(db: Any, *, grant_to_user: str = "") -> None:
    """Run all DDL statements idempotently."""
    logger.info(
        "Running agent shared-state HANA migrations (%d statements)...",
        len(_DDL_STATEMENTS),
    )
    applied = 0
    skipped = 0

    for stmt in _DDL_STATEMENTS:
        try:
            db.execute_write(stmt.strip())
            applied += 1
        except Exception as exc:
            error_code = getattr(exc, "errorcode", None)
            if error_code in (_ERR_DUPLICATE_TABLE, _ERR_DUPLICATE_INDEX):
                skipped += 1
                logger.debug("Already exists, skipping: %s", exc)
            else:
                logger.error("Migration failed: %s\nStatement: %s", exc, stmt.strip())
                raise

    if grant_to_user:
        logger.info(
            "Granting DML privileges on %d tables to '%s'...",
            len(_TABLE_NAMES),
            grant_to_user,
        )
        for table_name in _TABLE_NAMES:
            grant_sql = (
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table_name}" '
                f'TO "{grant_to_user}"'
            )
            try:
                db.execute_write(grant_sql)
            except Exception as exc:
                logger.warning(
                    "GRANT on %s to %s failed (may be owner): %s",
                    table_name,
                    grant_to_user,
                    exc,
                )

    logger.info("Migrations complete: %d applied, %d skipped.", applied, skipped)
