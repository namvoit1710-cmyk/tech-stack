-- Environment registry (SA-2365): a tenant-scoped, section-grouped set of named
-- config variables a workflow selects at run time to resolve {{$env.KEY}}.
--
-- VARIABLES is a JSON array of {key, type, value, values}. A credential-type
-- variable stores only a credential_ref id in `value` (never a secret). VERSION
-- is the CAS counter and IS_ACTIVE soft-deletes.
--
-- Natural key (TENANT_ID, SECTION, NAME) uniqueness is enforced in the repo for
-- ACTIVE rows only (so a soft-deleted env can be recreated), mirroring the
-- in-memory repo — hence a NON-unique lookup index here rather than a unique one.

CREATE COLUMN TABLE "AIW_ENVIRONMENT" (
    "ID"          NVARCHAR(36)   NOT NULL,
    "TENANT_ID"   NVARCHAR(128)  NOT NULL,
    "SECTION"     NVARCHAR(256)  NOT NULL DEFAULT '',
    "NAME"        NVARCHAR(128)  NOT NULL,
    "VARIABLES"   NCLOB,
    "IS_ACTIVE"   INTEGER        NOT NULL DEFAULT 1,
    "CREATED_BY"  NVARCHAR(128)  NOT NULL,
    "CREATED_AT"  NVARCHAR(64)   NOT NULL,
    "UPDATED_BY"  NVARCHAR(128)  NOT NULL,
    "UPDATED_AT"  NVARCHAR(64)   NOT NULL,
    "VERSION"     INTEGER        NOT NULL DEFAULT 1,
    PRIMARY KEY ("ID")
);

-- Lookup path for get_by_natural_key / tenant-scoped list.
CREATE INDEX "IDX_AIW_ENV_NATURAL"
    ON "AIW_ENVIRONMENT" ("TENANT_ID", "SECTION", "NAME");
