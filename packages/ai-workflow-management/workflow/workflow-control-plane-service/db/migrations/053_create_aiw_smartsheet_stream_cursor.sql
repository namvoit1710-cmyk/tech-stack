-- Migration: durable Smartsheet Event Reporting stream cursor (smartsheet-event-trigger S1)
--
-- Backs HanaSmartsheetCursorRepository. The poller drains GET /2.0/events, which
-- paginates via a nextStreamPosition token. Persisting it lets the poller resume
-- across restarts without losing or replaying events. One row per source/tenant
-- (ID = e.g. "smartsheet:<tenant_id>"). STREAM_POSITION is NCLOB — the token is
-- opaque and can be long.

CREATE COLUMN TABLE "AIW_SMARTSHEET_STREAM_CURSOR" (
    "ID"              NVARCHAR(128) NOT NULL,
    "STREAM_POSITION" NCLOB,
    "UPDATED_AT"      NVARCHAR(64),
    PRIMARY KEY ("ID")
);
