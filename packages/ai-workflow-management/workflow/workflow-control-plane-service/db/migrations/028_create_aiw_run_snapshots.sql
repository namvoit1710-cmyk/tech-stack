-- Phase 4.5 — projector snapshot store.
--
-- Persists periodic ``ProjectedRunState`` materialisations keyed by
-- ``(RUN_ID, SEQUENCE)`` so cold-cache replay in the control plane
-- becomes O(tail-events) instead of O(|total log|) per run.
--
-- Retention: the cached view store enforces K-back retention on each
-- write via ``delete_older_than``. Steady-state size is ≈ K rows per
-- active run, ~5 KB per row. No separate sweeper job required.
--
-- Schema versioning: ``SCHEMA_VERSION`` gates reader acceptance — a
-- snapshot written by an older reducer is ignored on load and falls
-- back to full replay. See
-- ``app/layer1_domain/services/projector/snapshot_codec.py``.

CREATE COLUMN TABLE "AIW_RUN_SNAPSHOTS" (
    "RUN_ID"         NVARCHAR(64)   NOT NULL,
    "SEQUENCE"       INTEGER        NOT NULL,
    "SCHEMA_VERSION" INTEGER        NOT NULL,
    "STATE_JSON"     NCLOB          NOT NULL,
    "CREATED_AT"     NVARCHAR(64)   NOT NULL,
    PRIMARY KEY ("RUN_ID", "SEQUENCE")
);

-- Load-latest hot path: ORDER BY SEQUENCE DESC + schema filter.
-- The primary key already serves this as an index scan in the correct
-- direction, but we add an explicit non-unique secondary index on
-- (RUN_ID, SEQUENCE, SCHEMA_VERSION) to let HANA cover the
-- ``load_latest`` query with an index-only lookup.
CREATE INDEX "IDX_AIW_RUN_SNAPSHOTS_SCHEMA"
    ON "AIW_RUN_SNAPSHOTS" ("RUN_ID", "SCHEMA_VERSION", "SEQUENCE");
