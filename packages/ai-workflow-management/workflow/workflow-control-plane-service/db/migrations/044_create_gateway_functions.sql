-- Gateway Function registry — named operations exposed by a GatewayApp.
--
-- Each row is one callable operation on AIW_GATEWAY_APPS(APP_ID). The request
-- signature is METHOD + PATH_TEMPLATE (with {placeholder} tokens) + INPUT_SCHEMA
-- (JSON array of params, each tagged with a location: path/query/header/body).
-- OUTPUT_SCHEMA and TAGS are JSON arrays. NODE_DEFINITION_ID back-links to the
-- auto-generated palette node (populated in a later phase). VERSION is the CAS
-- counter and IS_ACTIVE soft-deletes.

CREATE COLUMN TABLE "AIW_GATEWAY_FUNCTIONS" (
    "ID"                  NVARCHAR(36)    NOT NULL,
    "APP_ID"              NVARCHAR(36)    NOT NULL,
    "NAME"                NVARCHAR(128)   NOT NULL,
    "DESCRIPTION"         NVARCHAR(512),
    "METHOD"              NVARCHAR(16)    NOT NULL,
    "PATH_TEMPLATE"       NVARCHAR(1024)  NOT NULL,
    "INPUT_SCHEMA"        NCLOB,
    "OUTPUT_SCHEMA"       NCLOB,
    "TIMEOUT_MS"          INTEGER         NOT NULL DEFAULT 30000,
    "ICON"                NVARCHAR(64),
    "COLOR"               NVARCHAR(32),
    "TAGS"                NCLOB,
    "IS_ACTIVE"           INTEGER         NOT NULL DEFAULT 1,
    "NODE_DEFINITION_ID"  NVARCHAR(64),
    "CREATED_BY"          NVARCHAR(128)   NOT NULL,
    "CREATED_AT"          NVARCHAR(64)    NOT NULL,
    "UPDATED_BY"          NVARCHAR(128)   NOT NULL,
    "UPDATED_AT"          NVARCHAR(64)    NOT NULL,
    "VERSION"             INTEGER         NOT NULL DEFAULT 1,
    PRIMARY KEY ("ID")
);

-- Function name is unique within an app.
CREATE UNIQUE INDEX "IDX_AIW_GW_FUNCS_APP_NAME"
    ON "AIW_GATEWAY_FUNCTIONS" ("APP_ID", "NAME");

-- List-by-app lookups.
CREATE INDEX "IDX_AIW_GW_FUNCS_APP"
    ON "AIW_GATEWAY_FUNCTIONS" ("APP_ID");
