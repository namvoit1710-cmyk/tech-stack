-- Migration: durable-timer store (SA-1371)
--
-- Backs HanaTimerRepository. A pending Timer is a persisted intent to fire a
-- TimerFired event at FIRE_AT — the leader-gated TimerDispatcher scans FIRE_AT
-- <= now each tick and retires the row after firing. Keyed by the deterministic
-- TIMER_ID so a re-scheduled / replayed timer upserts a single row.
--
-- The event log (AIW_EVENT_LOG) remains authoritative — this table is a
-- convenience index the dispatcher scans — it is rebuildable from the log's
-- TimerScheduled / TimerFired / TimerCancelled events.

CREATE COLUMN TABLE "AIW_TIMERS" (
    "TIMER_ID"     NVARCHAR(64)   NOT NULL,
    "RUN_ID"       NVARCHAR(256)  NOT NULL,
    "WORKFLOW_ID"  NVARCHAR(64),
    "NODE_ID"      NVARCHAR(64),
    "KIND"         NVARCHAR(32),
    "FIRE_AT"      NVARCHAR(64),
    "CREATED_AT"   NVARCHAR(64),
    "METADATA"     NCLOB,
    PRIMARY KEY ("TIMER_ID")
);

-- list_due scans FIRE_AT <= :now — index the range read.
CREATE INDEX "IDX_AIW_TIMERS_FIRE_AT" ON "AIW_TIMERS" ("FIRE_AT");
