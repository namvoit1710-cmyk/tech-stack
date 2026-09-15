-- Gateway App registry (supersedes 038 AIW_GATEWAY_INTERNAL_SERVICES, dropped in 045).
--
-- One row per registered application the gateway can proxy to. KIND selects the
-- target style:
--   internal -> BASE_URL + injected credential (AUTH_TYPE/AUTH_CONFIG_ENCRYPTED)
--   external -> ALLOWED_HOSTS (JSON array of bare hosts / one-level wildcards)
--
-- AUTH_CONFIG_ENCRYPTED is an AES-256-GCM ciphertext blob whose KEK version is
-- pinned per row in AUTH_CONFIG_KEY_VERSION for online rotation. NULL/empty when
-- AUTH_TYPE is 'none'. VERSION is the CAS counter and IS_ACTIVE soft-deletes.

CREATE COLUMN TABLE "AIW_GATEWAY_APPS" (
    "ID"                       NVARCHAR(36)   NOT NULL,
    "NAME"                     NVARCHAR(128)  NOT NULL,
    "KIND"                     NVARCHAR(16)   NOT NULL,
    "BASE_URL"                 NVARCHAR(512),
    "ALLOWED_HOSTS"            NCLOB,
    "AUTH_TYPE"                NVARCHAR(32)   NOT NULL,
    "AUTH_CONFIG_ENCRYPTED"    NCLOB,
    "AUTH_CONFIG_KEY_VERSION"  INTEGER        NOT NULL DEFAULT 0,
    "IS_ACTIVE"                INTEGER        NOT NULL DEFAULT 1,
    "DESCRIPTION"              NVARCHAR(512),
    "CREATED_BY"               NVARCHAR(128)  NOT NULL,
    "CREATED_AT"               NVARCHAR(64)   NOT NULL,
    "UPDATED_BY"               NVARCHAR(128)  NOT NULL,
    "UPDATED_AT"               NVARCHAR(64)   NOT NULL,
    "VERSION"                  INTEGER        NOT NULL DEFAULT 1,
    PRIMARY KEY ("ID")
);

-- Name is the public lookup key — unique across all rows.
CREATE UNIQUE INDEX "IDX_AIW_GW_APPS_NAME"
    ON "AIW_GATEWAY_APPS" ("NAME");
