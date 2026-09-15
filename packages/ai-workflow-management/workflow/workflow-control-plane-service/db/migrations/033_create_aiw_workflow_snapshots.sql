-- Workflow-snapshot store.
--
-- Persists the immutable workflow definition each ``WorkflowRun`` was
-- started against. ``WorkflowRun.workflow_snapshot_id`` is the FK back to
-- this table — the engine reads the definition through the snapshot
-- repository (``OrchestrationEngine._load_workflow_for_run``) instead of
-- the live ``WORKFLOWS`` table, so mid-run edits cannot leak into
-- in-flight runs.
--
-- Lifetime: snapshots are immutable once written. Cleanup is the run
-- lifecycle's responsibility — when a run row is deleted, its snapshot
-- row can be deleted alongside (no FK constraint enforced here to
-- avoid coupling delete ordering).
--
-- Future: content-hash dedup. ``DEFINITION_JSON`` could be hashed and
-- looked up before insert so two runs of an unchanged workflow share a
-- row. Not implemented in the initial cut.

CREATE COLUMN TABLE "AIW_WORKFLOW_SNAPSHOTS" (
    "ID"               NVARCHAR(64)   NOT NULL,
    "WORKFLOW_ID"      NVARCHAR(64)   NOT NULL,
    "WORKFLOW_VERSION" NVARCHAR(32)   NOT NULL,
    "DEFINITION_JSON"  NCLOB          NOT NULL,
    "CREATED_AT"       NVARCHAR(64)   NOT NULL,
    PRIMARY KEY ("ID")
);

-- Workflow-scoped lookup (e.g. "all snapshots ever taken for this workflow"
-- — diagnostic / debug only — the engine hot path is get_by_id).
CREATE INDEX "IDX_AIW_WORKFLOW_SNAPSHOTS_WORKFLOW"
    ON "AIW_WORKFLOW_SNAPSHOTS" ("WORKFLOW_ID");
