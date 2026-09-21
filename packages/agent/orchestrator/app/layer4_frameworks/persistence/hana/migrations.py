"""Idempotent DDL migrations for the agent orchestrator HANA tables.

Each statement is executed individually. HANA error code 288
("cannot use duplicate table name") is caught and silently skipped
so the migration is re-runnable.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# HANA error code for "duplicate table name"
_ERR_DUPLICATE_TABLE = 288
# HANA error code for "duplicate index name"
_ERR_DUPLICATE_INDEX = 289


def _is_idempotent_ddl_error(exc: Exception) -> bool:
    error_code = getattr(exc, "errorcode", None)
    if error_code in (_ERR_DUPLICATE_TABLE, _ERR_DUPLICATE_INDEX):
        return True

    message = str(exc).lower()
    return "duplicate column" in message or "already exists" in message

_DDL_STATEMENTS: list[str] = [
    # ── AIW_USER_PROFILES ──
    """
    CREATE COLUMN TABLE "AIW_USER_PROFILES" (
        "USER_ID"               NVARCHAR(128)  NOT NULL,
        "TENANT_ID"             NVARCHAR(64)   NOT NULL,
        "EXPERTISE_LEVEL"       NVARCHAR(32)   NOT NULL DEFAULT 'beginner',
        "PREFERRED_DOMAINS"     NCLOB,
        "INTERACTION_COUNT"     INTEGER        NOT NULL DEFAULT 0,
        "SUCCESSFUL_WORKFLOWS"  INTEGER        NOT NULL DEFAULT 0,
        "FAILED_WORKFLOWS"      INTEGER        NOT NULL DEFAULT 0,
        "MOST_USED_AGENTS"      NCLOB,
        "PRIVACY_SETTINGS"      NCLOB,
        "PREFERENCES"           NCLOB,
        "CREATED_AT"            NVARCHAR(64)   NOT NULL,
        "UPDATED_AT"            NVARCHAR(64)   NOT NULL,
        PRIMARY KEY ("USER_ID", "TENANT_ID")
    )
    """,

    # ── AIW_FLOW_CHECKPOINTS (conversational flow checkpointing) ──
    """
    CREATE COLUMN TABLE "AIW_FLOW_CHECKPOINTS" (
        "CHECKPOINT_ID"     NVARCHAR(512)  NOT NULL PRIMARY KEY,
        "THREAD_ID"         NVARCHAR(128)  NOT NULL,
        "THREAD_TS"         NVARCHAR(64)   NOT NULL,
        "CHECKPOINT_DATA"   NCLOB,
        "METADATA"          NCLOB,
        "PARENT_CONFIG"     NCLOB,
        "CREATED_AT"        NVARCHAR(64)   NOT NULL
    )
    """,
    """CREATE INDEX "IDX_AIW_CHECKPOINTS_THREAD" ON "AIW_FLOW_CHECKPOINTS" ("THREAD_ID", "THREAD_TS")""",

    # ── AIW_FLOW_CHECKPOINT_WRITES (pending writes for checkpoints) ──
    """
    CREATE COLUMN TABLE "AIW_FLOW_CHECKPOINT_WRITES" (
        "WRITE_ID"          NVARCHAR(1024) NOT NULL PRIMARY KEY,
        "THREAD_ID"         NVARCHAR(128)  NOT NULL,
        "CHECKPOINT_ID"     NVARCHAR(512)  NOT NULL,
        "TASK_ID"           NVARCHAR(128)  NOT NULL,
        "CHANNEL"           NVARCHAR(256)  NOT NULL,
        "VALUE"             NCLOB,
        "CREATED_AT"        NVARCHAR(64)   NOT NULL
    )
    """,
    """CREATE INDEX "IDX_AIW_CKP_WRITES_THREAD" ON "AIW_FLOW_CHECKPOINT_WRITES" ("THREAD_ID", "CHECKPOINT_ID")""",

    # ── AIW_CONVERSATION_RELATIONS ──
    """
    CREATE COLUMN TABLE "AIW_SUB_CONVERSATION_RELATIONS" (
        "CONVERSATION_ID"        NVARCHAR(128)  NOT NULL PRIMARY KEY,
        "PARENT_CONVERSATION_ID" NVARCHAR(128),
        "CONTEXT_ID"             NVARCHAR(128)  NOT NULL,
        "NODE_ID"                NVARCHAR(128),
        "CREATED_AT"             NVARCHAR(64)   NOT NULL,
        "UPDATED_AT"             NVARCHAR(64)   NOT NULL
    );
    """,
    """
    CREATE COLUMN TABLE "AIW_CONVERSATION_CONTEXTS" (
        "CONTEXT_ID"             NVARCHAR(128)  NOT NULL PRIMARY KEY,
        "RUN_ID"                 NVARCHAR(128)  NOT NULL,
        "WORKFLOW_ID"            NVARCHAR(128),
        "WORKFLOW_NAME"          NVARCHAR(256)  NOT NULL DEFAULT '',
        "WORKFLOW_STATUS"        NVARCHAR(64)   NOT NULL DEFAULT 'PENDING',
        "STATUS"                 NVARCHAR(64)   NOT NULL DEFAULT 'ACTIVATED',
        "USER_ID"                NVARCHAR(128)  NOT NULL,
        "TENANT_ID"              NVARCHAR(128)  NOT NULL,
        "CREATED_AT"             NVARCHAR(64)   NOT NULL,
        "UPDATED_AT"             NVARCHAR(64)   NOT NULL
    );
    """,
    "ALTER TABLE \"AIW_CONVERSATION_CONTEXTS\" ADD (\"WORKFLOW_NAME\" NVARCHAR(256) DEFAULT '')",
    "ALTER TABLE \"AIW_CONVERSATION_CONTEXTS\" ADD (\"WORKFLOW_STATUS\" NVARCHAR(64) DEFAULT 'PENDING')",
    "ALTER TABLE \"AIW_CONVERSATION_CONTEXTS\" ADD (\"STATUS\" NVARCHAR(64) DEFAULT 'ACTIVATED')",

    # ── AIW_AGENT_EVENT ──
    """
    CREATE COLUMN TABLE "AIW_AGENT_EVENT" (
        "EVENT_ID"         NVARCHAR(128)  NOT NULL PRIMARY KEY,
        "ROLE"             NVARCHAR(32)   DEFAULT 'assistant',
        "EVENT_TYPE"       NVARCHAR(128)  NOT NULL,
        "TIMESTAMP"        NVARCHAR(64)   NOT NULL,
        "CORRELATION_ID"   NVARCHAR(128)  NOT NULL,
        "CONV_ID"          NVARCHAR(128)  NOT NULL,
        "METADATA"         NCLOB,
        "PAYLOAD"          NCLOB
    )
    """,
    "ALTER TABLE \"AIW_AGENT_EVENT\" ADD (\"ROLE\" NVARCHAR(32) DEFAULT 'assistant')",
    """CREATE INDEX "IDX_AIW_AGENT_EVENT_CONV" ON "AIW_AGENT_EVENT" ("CONV_ID")""",
    """CREATE INDEX "IDX_AIW_AGENT_EVENT_CORR" ON "AIW_AGENT_EVENT" ("CORRELATION_ID")""",
    """CREATE INDEX "IDX_AGENT_EVENT_CONV_TYPE_TIME_ID" ON "AIW_AGENT_EVENT" ("CONV_ID", "EVENT_TYPE", "TIMESTAMP", "EVENT_ID")""",
    # ── AIW_CHAT_TIMELINE_NODE ──
    """
    CREATE COLUMN TABLE "AIW_CHAT_TIMELINE_NODE" (
        "CONV_ID"          NVARCHAR(128)  NOT NULL,
        "PAYLOAD_ID"       NVARCHAR(128)  NOT NULL,
        "LATEST_EVENT_ID"  NVARCHAR(128)  NOT NULL,
        "CREATED_AT"       TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
        "UPDATED_AT"       TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY ("CONV_ID", "PAYLOAD_ID")
    )
    """,
    """CREATE INDEX "IDX_AIW_TIMELINE_NODE_CONV_ORDER" ON "AIW_CHAT_TIMELINE_NODE" ("CONV_ID", "CREATED_AT")""",
    """CREATE INDEX "IDX_AIW_TIMELINE_NODE_EVENT" ON "AIW_CHAT_TIMELINE_NODE" ("LATEST_EVENT_ID")""",
    # ── AIW_CONVERSATION_SUMMARY ──
    """
    CREATE COLUMN TABLE "AIW_CONVERSATION_SUMMARY" (
        "CONV_ID"          NVARCHAR(128)  NOT NULL PRIMARY KEY,
        "TITLE"            NVARCHAR(500)  NOT NULL,
        "TITLE_SOURCE"     NVARCHAR(64)   NOT NULL,
        "CREATED_AT"       TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
        "UPDATED_AT"       TIMESTAMP      DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "ALTER TABLE \"AIW_CONVERSATION_SUMMARY\" ADD (\"TITLE_SOURCE\" NVARCHAR(64) DEFAULT 'user_message_fallback')",

    # ── AIW_AGENT_MEMORY (SA-1436: orchestrator-owned long-term memory) ──
    """
    CREATE COLUMN TABLE "AIW_AGENT_MEMORY" (
        "MEMORY_ID"        NVARCHAR(128) NOT NULL PRIMARY KEY,
        "TENANT_ID"        NVARCHAR(128) NOT NULL,

        "USER_ID"          NVARCHAR(128),
        "AGENT_ID"         NVARCHAR(128),
        "WORKFLOW_ID"      NVARCHAR(128),
        "CONV_ID"          NVARCHAR(128),

        "SCOPE"            NVARCHAR(32)  NOT NULL,
        "MEMORY_TYPE"      NVARCHAR(32)  NOT NULL,
        "MEMORY_KEY"       NVARCHAR(256),

        "CONTENT"          NCLOB         NOT NULL,
        "SUMMARY"          NVARCHAR(1000),

        "SOURCE_CONV_ID"   NVARCHAR(128),
        "SOURCE_EVENT_ID"  NVARCHAR(128),
        "SOURCE_TASK_ID"   NVARCHAR(128),

        "CONFIDENCE"       DECIMAL(5,4)  DEFAULT 0.8000,
        "IMPORTANCE"       INTEGER       DEFAULT 3,
        "SENSITIVITY"      NVARCHAR(32)  DEFAULT 'normal',
        "STATUS"           NVARCHAR(32)  DEFAULT 'active',

        "VALID_UNTIL"      TIMESTAMP,
        "LAST_ACCESSED_AT" TIMESTAMP,
        "ACCESS_COUNT"     INTEGER       DEFAULT 0,

        "METADATA_JSON"    NCLOB,

        "CREATED_AT"       TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
        "UPDATED_AT"       TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """CREATE INDEX "IDX_AIW_AGENT_MEMORY_LOOKUP" ON "AIW_AGENT_MEMORY" ("TENANT_ID", "USER_ID", "AGENT_ID", "WORKFLOW_ID", "MEMORY_TYPE", "STATUS")""",
    """CREATE INDEX "IDX_AIW_AGENT_MEMORY_SOURCE_CONV" ON "AIW_AGENT_MEMORY" ("SOURCE_CONV_ID")""",
    """CREATE INDEX "IDX_AIW_AGENT_MEMORY_UPDATED" ON "AIW_AGENT_MEMORY" ("UPDATED_AT")""",

    # ── AIW_AGENT_MEMORY_EVENT (audit: created / forgotten / accessed) ──
    """
    CREATE COLUMN TABLE "AIW_AGENT_MEMORY_EVENT" (
        "EVENT_ID"      NVARCHAR(128) NOT NULL PRIMARY KEY,
        "MEMORY_ID"     NVARCHAR(128),
        "TENANT_ID"     NVARCHAR(128) NOT NULL,
        "USER_ID"       NVARCHAR(128),
        "EVENT_TYPE"    NVARCHAR(64)  NOT NULL,
        "REASON"        NVARCHAR(1000),
        "PAYLOAD_JSON"  NCLOB,
        "CREATED_AT"    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """CREATE INDEX "IDX_AIW_AGENT_MEMORY_EVENT_MEMORY" ON "AIW_AGENT_MEMORY_EVENT" ("MEMORY_ID")""",
]


_TABLE_NAMES = [
    "AIW_USER_PROFILES",
    "AIW_FLOW_CHECKPOINTS",
    "AIW_FLOW_CHECKPOINT_WRITES",
    "AIW_AGENT_EVENT",
    "AIW_CHAT_TIMELINE_NODE",
    "AIW_CONVERSATION_SUMMARY",
    "AIW_AGENT_MEMORY",
    "AIW_AGENT_MEMORY_EVENT",
]


def migrate(db: Any, *, grant_to_user: str = "") -> None:
    """Run all DDL statements idempotently."""
    logger.info("Running orchestrator HANA migrations (%d statements)...", len(_DDL_STATEMENTS))
    applied = 0
    skipped = 0

    for stmt in _DDL_STATEMENTS:
        try:
            db.execute_write(stmt.strip())
            applied += 1
        except Exception as exc:
            if _is_idempotent_ddl_error(exc):
                skipped += 1
                logger.debug("Already exists, skipping: %s", exc)
            else:
                logger.error("Migration failed: %s\nStatement: %s", exc, stmt.strip())
                raise

    if grant_to_user:
        logger.info("Granting DML privileges on %d tables to '%s'...", len(_TABLE_NAMES), grant_to_user)
        for table_name in _TABLE_NAMES:
            grant_sql = f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table_name}" TO "{grant_to_user}"'
            try:
                db.execute_write(grant_sql)
            except Exception as exc:
                logger.warning("GRANT on %s to %s failed (may be owner): %s", table_name, grant_to_user, exc)

    logger.info("Migrations complete: %d applied, %d skipped.", applied, skipped)
