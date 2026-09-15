-- SA-2437: per-workflow worker-instance pin (debug routing). One row per flow id.
CREATE COLUMN TABLE "AIW_WORKFLOW_WORKER_CONFIG" (
    "WORKFLOW_ID"  NVARCHAR(64)   NOT NULL PRIMARY KEY,
    "TENANT_ID"    NVARCHAR(256),
    "MODE"         NVARCHAR(32),
    "SELECTION"    NCLOB,
    "UPDATED_AT"   NVARCHAR(64),
    "UPDATED_BY"   NVARCHAR(256)
);
