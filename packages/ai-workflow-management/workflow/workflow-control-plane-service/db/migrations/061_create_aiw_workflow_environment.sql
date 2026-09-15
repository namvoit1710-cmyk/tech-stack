-- Workflow↔Section binding (SA-2450): records which SECTION a workflow is bound
-- to and which of that section's environments it may run against (subset ≥1).
--
-- One ACTIVE row per (TENANT_ID, WORKFLOW_ID) — a workflow binds to exactly one
-- section. ENVIRONMENT_IDS is a JSON array of AIW_ENVIRONMENT ids (all belonging
-- to SECTION; that cross-table rule is enforced in the use case). VERSION is the
-- CAS counter and IS_ACTIVE soft-deletes. Uniqueness on ACTIVE rows is enforced
-- in the repo (so a soft-deleted binding can be recreated) — hence a non-unique
-- lookup index here, mirroring AIW_ENVIRONMENT (migration 060).

CREATE COLUMN TABLE "AIW_WORKFLOW_ENVIRONMENT" (
    "ID"               NVARCHAR(36)   NOT NULL,
    "TENANT_ID"        NVARCHAR(128)  NOT NULL,
    "WORKFLOW_ID"      NVARCHAR(64)   NOT NULL,
    "SECTION"          NVARCHAR(256)  NOT NULL DEFAULT '',
    "ENVIRONMENT_IDS"  NCLOB,
    "IS_ACTIVE"        INTEGER        NOT NULL DEFAULT 1,
    "CREATED_BY"       NVARCHAR(128)  NOT NULL,
    "CREATED_AT"       NVARCHAR(64)   NOT NULL,
    "UPDATED_BY"       NVARCHAR(128)  NOT NULL,
    "UPDATED_AT"       NVARCHAR(64)   NOT NULL,
    "VERSION"          INTEGER        NOT NULL DEFAULT 1,
    PRIMARY KEY ("ID")
);

-- Lookup path for get_by_workflow / tenant-scoped reverse list.
CREATE INDEX "IDX_AIW_WF_ENV_WORKFLOW"
    ON "AIW_WORKFLOW_ENVIRONMENT" ("TENANT_ID", "WORKFLOW_ID");
