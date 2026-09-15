-- Section↔Environment membership (SA-2605): the MANY-TO-MANY between a section and
-- the environments it groups. One ACTIVE row per (TENANT_ID, SECTION) holds the
-- section's environment ids as a JSON array (mirrors AIW_WORKFLOW_ENVIRONMENT's
-- ENVIRONMENT_IDS). An environment id may appear under several sections, and a
-- section may hold many environments — this replaces the single AIW_ENVIRONMENT.
-- SECTION pointer (SA-2365/2471) as the source of truth for membership. Reverse
-- lookup (environment → sections) is an in-process scan (row count is small — one
-- row per section). Uniqueness on ACTIVE rows enforced in the repo; non-unique index.

CREATE COLUMN TABLE "AIW_SECTION_ENVIRONMENT" (
    "ID"              NVARCHAR(36)  NOT NULL,
    "TENANT_ID"       NVARCHAR(128) NOT NULL,
    "SECTION"         NVARCHAR(256) NOT NULL,
    "ENVIRONMENT_IDS" NCLOB,
    "IS_ACTIVE"       INTEGER       NOT NULL DEFAULT 1,
    "CREATED_BY"      NVARCHAR(128) NOT NULL,
    "CREATED_AT"      NVARCHAR(64)  NOT NULL,
    "UPDATED_BY"      NVARCHAR(128) NOT NULL,
    "UPDATED_AT"      NVARCHAR(64)  NOT NULL,
    "VERSION"         INTEGER       NOT NULL DEFAULT 1,
    PRIMARY KEY ("ID")
);

-- Lookup path for get_by_section / tenant-scoped list (reverse env→sections scans).
CREATE INDEX "IDX_AIW_SEC_ENV_SECTION"
    ON "AIW_SECTION_ENVIRONMENT" ("TENANT_ID", "SECTION");
