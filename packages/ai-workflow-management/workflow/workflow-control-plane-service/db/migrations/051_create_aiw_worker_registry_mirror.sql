-- Migration: worker-registry observability mirror (SA-2050, R3)
--
-- A durable, human-queryable MIRROR of the executor's in-memory worker
-- registry. A CP background loop snapshot-reconciles list_workers() into this
-- table every WORKER_MIRROR_INTERVAL_SECONDS so an operator can see the live
-- replicas in DBeaver/SQL. READ-ONLY for humans — nothing reads this table for
-- dispatch or the palette (those stay 100% on the in-memory registry).
--
-- PK is ID (the registration key "instance_id:worker_type", or a uuid for
-- local). INSTANCE_ID is derived from ID (best-effort grouping column). The
-- WORKER_TYPE index backs the load-bearing "list the N replicas of a type"
-- query. HOST/PID/STARTED_AT are best-effort — stored NULL when the executor's
-- ListWorkersOutput DTO does not expose them (spec §4.5).

CREATE COLUMN TABLE "AIW_WORKER_REGISTRY_MIRROR" (
    "ID"             NVARCHAR(256) NOT NULL,
    "INSTANCE_ID"    NVARCHAR(256),
    "WORKER_TYPE"    NVARCHAR(128),
    "HEALTH_STATUS"  NVARCHAR(32),
    "LAST_HEARTBEAT" NVARCHAR(64),
    "ENDPOINT_URL"   NVARCHAR(512),
    "VERSION"        NVARCHAR(64),
    "SPEC_VERSION"   NVARCHAR(64),
    "KIND"           NVARCHAR(32),
    "HOST"           NVARCHAR(256),
    "PID"            INTEGER,
    "STARTED_AT"     NVARCHAR(64),
    "FIRST_SEEN_AT"  NVARCHAR(64),
    "LAST_SEEN_AT"   NVARCHAR(64),
    PRIMARY KEY ("ID")
);

CREATE INDEX "IDX_AIW_WORKER_MIRROR_TYPE" ON "AIW_WORKER_REGISTRY_MIRROR" ("WORKER_TYPE");
