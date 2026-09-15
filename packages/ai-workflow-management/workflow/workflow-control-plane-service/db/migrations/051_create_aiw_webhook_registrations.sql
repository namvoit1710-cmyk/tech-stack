-- Migration: durable inbound-webhook store (SA-2059 F2)
--
-- Backs HanaWebhookRegistrationRepository. A WebhookRegistration is the ingress
-- side of SA-2059 B7: an external source POSTs to POST /webhooks/{path}, the CP
-- verifies + normalizes + publishes onto EMIT_TOPIC. Registrations were
-- memory-only and lost on restart, so this makes them durable. Keyed by ID with
-- PATH indexed because the ingress hot path resolves a registration by path per
-- inbound POST.
--
-- IP_ALLOWLIST is a list -> stored as a JSON array in an NCLOB column.

CREATE COLUMN TABLE "AIW_WEBHOOK_REGISTRATIONS" (
    "ID"               NVARCHAR(64)   NOT NULL,
    "NAME"             NVARCHAR(256),
    "PATH"             NVARCHAR(512),
    "SOURCE"           NVARCHAR(64),
    "SECRET"           NVARCHAR(512),
    "HMAC_SECRET"      NVARCHAR(512),
    "EMIT_TOPIC"       NVARCHAR(512),
    "IP_ALLOWLIST"     NCLOB,
    "INTEGRATION_ACTOR" NVARCHAR(512),
    "TENANT_ID"        NVARCHAR(256),
    "ENABLED"          NVARCHAR(8),
    "CREATED_AT"       NVARCHAR(64),
    "UPDATED_AT"       NVARCHAR(64),
    PRIMARY KEY ("ID")
);

-- get_by_path resolves a registration on every inbound POST — index the lookup.
CREATE INDEX "IDX_AIW_WEBHOOK_REGISTRATIONS_PATH" ON "AIW_WEBHOOK_REGISTRATIONS" ("PATH");

-- Recent-ingress log (the FE Deliveries tab). Bounded newest-first per
-- registration, with growth trimmed by the repo on each record_delivery.
CREATE COLUMN TABLE "AIW_WEBHOOK_DELIVERIES" (
    "ID"               NVARCHAR(64)   NOT NULL,
    "REGISTRATION_ID"  NVARCHAR(64),
    "RECEIVED_AT"      NVARCHAR(64),
    "OUTCOME"          NVARCHAR(32),
    "STATUS_CODE"      INTEGER,
    "EVENT_TOPIC"      NVARCHAR(512),
    "EVENT_ID"         NVARCHAR(256),
    "DETAIL"           NCLOB,
    PRIMARY KEY ("ID")
);

-- list_deliveries filters by REGISTRATION_ID ordered by RECEIVED_AT desc.
CREATE INDEX "IDX_AIW_WEBHOOK_DELIVERIES_REG_ID" ON "AIW_WEBHOOK_DELIVERIES" ("REGISTRATION_ID");
