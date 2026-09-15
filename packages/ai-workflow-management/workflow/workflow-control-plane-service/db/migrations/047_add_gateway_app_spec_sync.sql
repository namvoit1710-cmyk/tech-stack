-- Gateway App OpenAPI spec sync — import & keep functions in sync with an upstream spec.
--
-- SPEC_URL       — the OpenAPI document URL. Empty/NULL = no sync. A background
--                  poller and the on-demand POST /gateway/apps/{name}/sync
--                  re-fetch it and reconcile this app's functions to match.
-- LAST_SYNCED_AT — ISO timestamp of the last successful sync (NULL/"" = never).
-- SPEC_HASH      — sha256 of the last fetched body, letting a sync skip parse+write
--                  when the upstream document is unchanged.

ALTER TABLE "AIW_GATEWAY_APPS" ADD (
    "SPEC_URL"        NVARCHAR(1024) DEFAULT NULL,
    "LAST_SYNCED_AT"  NVARCHAR(64)   DEFAULT NULL,
    "SPEC_HASH"       NVARCHAR(64)   DEFAULT NULL
);
