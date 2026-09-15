-- Workflow→Section binding (SA-2605): a workflow binds to EXACTLY ONE section and
-- nothing more. Supersedes AIW_WORKFLOW_ENVIRONMENT (SA-2450): there is no per-flow
-- environment subset any more — a flow may use ANY environment of its bound section
-- at run time (section membership lives in AIW_SECTION_ENVIRONMENT). One ACTIVE row
-- per (TENANT_ID, WORKFLOW_ID); uniqueness on ACTIVE rows is enforced in the repo
-- (so a soft-deleted binding can be recreated) — hence a non-unique lookup index,
-- mirroring AIW_WORKFLOW_ENVIRONMENT (migration 061).

CREATE COLUMN TABLE "AIW_WORKFLOW_SECTION" (
    "ID"          NVARCHAR(36)  NOT NULL,
    "TENANT_ID"   NVARCHAR(128) NOT NULL,
    "WORKFLOW_ID" NVARCHAR(64)  NOT NULL,
    "SECTION"     NVARCHAR(256) NOT NULL DEFAULT '',
    "IS_ACTIVE"   INTEGER       NOT NULL DEFAULT 1,
    "CREATED_BY"  NVARCHAR(128) NOT NULL,
    "CREATED_AT"  NVARCHAR(64)  NOT NULL,
    "UPDATED_BY"  NVARCHAR(128) NOT NULL,
    "UPDATED_AT"  NVARCHAR(64)  NOT NULL,
    "VERSION"     INTEGER       NOT NULL DEFAULT 1,
    PRIMARY KEY ("ID")
);

-- Lookup path for get_by_workflow / tenant-scoped reverse list.
CREATE INDEX "IDX_AIW_WF_SECTION_WORKFLOW"
    ON "AIW_WORKFLOW_SECTION" ("TENANT_ID", "WORKFLOW_ID");
