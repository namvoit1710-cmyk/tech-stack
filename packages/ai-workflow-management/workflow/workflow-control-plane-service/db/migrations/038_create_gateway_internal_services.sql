-- Gateway-worker internal service registry.
--
-- Runtime-managed registry of internal services the gateway-worker can
-- proxy to via `call_internal`. Each row is one named upstream
-- (base_url + auth method + encrypted credential).
--
-- AUTH_CONFIG_ENCRYPTED holds an AES-256-GCM ciphertext blob whose
-- plaintext is the JSON shape that matches AUTH_TYPE:
--   bearer          -> {"token": "..."}
--   api_key_header  -> {"header": "X-API-Key", "value": "..."}
--   oauth_cc        -> {"token_url": "...", "client_id": "...", "client_secret": "..."}
--   none            -> {}
-- The KEK comes from env `GATEWAY_REGISTRY_KEK_V<n>` — the version is
-- pinned per row in AUTH_CONFIG_KEY_VERSION so rotation can be done
-- online (write new rows with v+1 while old rows still decrypt via v).
--
-- VERSION is the CAS counter (same pattern as Phase M.2.1 on workflows /
-- runs). Soft-delete via IS_ACTIVE preserves audit history.

CREATE COLUMN TABLE "AIW_GATEWAY_INTERNAL_SERVICES" (
    "ID"                       NVARCHAR(36)   NOT NULL,
    "NAME"                     NVARCHAR(128)  NOT NULL,
    "BASE_URL"                 NVARCHAR(512)  NOT NULL,
    "AUTH_TYPE"                NVARCHAR(32)   NOT NULL,
    "AUTH_CONFIG_ENCRYPTED"    NCLOB          NOT NULL,
    "AUTH_CONFIG_KEY_VERSION"  INTEGER        NOT NULL,
    "IS_ACTIVE"                INTEGER        NOT NULL DEFAULT 1,
    "DESCRIPTION"              NVARCHAR(512),
    "CREATED_BY"               NVARCHAR(128)  NOT NULL,
    "CREATED_AT"               NVARCHAR(64)   NOT NULL,
    "UPDATED_BY"               NVARCHAR(128)  NOT NULL,
    "UPDATED_AT"               NVARCHAR(64)   NOT NULL,
    "VERSION"                  INTEGER        NOT NULL DEFAULT 1,
    PRIMARY KEY ("ID")
);

-- Name is the public lookup key — must be unique across active rows.
CREATE UNIQUE INDEX "IDX_AIW_GW_INT_SVC_NAME"
    ON "AIW_GATEWAY_INTERNAL_SERVICES" ("NAME");
