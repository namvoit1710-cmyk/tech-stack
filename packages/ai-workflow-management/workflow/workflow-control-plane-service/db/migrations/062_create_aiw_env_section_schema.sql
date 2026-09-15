-- Section-level shared variable KEY SCHEMA (SA-2471): per (TENANT_ID, SECTION),
-- the single list of {key, type} pairs every Environment in that section shares.
-- Environments still store their own per-env values in AIW_ENVIRONMENT. This
-- table governs which keys/types those environments may hold (a save reconciles).
--
-- KEYS is a JSON array of {key, type}. No secrets are ever stored here — a schema
-- owns only key names and their types. VERSION is the CAS counter and IS_ACTIVE
-- soft-deletes.
--
-- Natural key (TENANT_ID, SECTION) uniqueness is enforced in the repo for ACTIVE
-- rows only (so a soft-deleted schema can be recreated), mirroring the in-memory
-- repo — hence a NON-unique lookup index here rather than a unique one.

CREATE COLUMN TABLE "AIW_ENV_SECTION_SCHEMA" (
    "ID"          NVARCHAR(36)   NOT NULL,
    "TENANT_ID"   NVARCHAR(128)  NOT NULL,
    "SECTION"     NVARCHAR(256)  NOT NULL DEFAULT '',
    "KEYS"        NCLOB,
    "IS_ACTIVE"   INTEGER        NOT NULL DEFAULT 1,
    "CREATED_BY"  NVARCHAR(128)  NOT NULL,
    "CREATED_AT"  NVARCHAR(64)   NOT NULL,
    "UPDATED_BY"  NVARCHAR(128)  NOT NULL,
    "UPDATED_AT"  NVARCHAR(64)   NOT NULL,
    "VERSION"     INTEGER        NOT NULL DEFAULT 1,
    PRIMARY KEY ("ID")
);

-- Lookup path for get_by_natural_key / tenant-scoped list.
CREATE INDEX "IDX_AIW_SEC_SCHEMA"
    ON "AIW_ENV_SECTION_SCHEMA" ("TENANT_ID", "SECTION");
