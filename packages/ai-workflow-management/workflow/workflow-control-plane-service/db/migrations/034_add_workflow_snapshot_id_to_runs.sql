-- Workflow-snapshot rollout (Phase A).
--
-- Adds the FK column ``WORKFLOW_SNAPSHOT_ID`` to ``AIW_WORKFLOW_RUNS``
-- pointing at ``AIW_WORKFLOW_SNAPSHOTS.ID`` (see migration 033). The
-- engine resolves the run's workflow definition through this id via
-- ``OrchestrationEngine._load_workflow_for_run``, replacing every live
-- ``workflow_repo.get_by_id(run.workflow_id)`` callback-time read.
--
-- Nullable for now: existing rows from before this migration have no
-- snapshot. Phase D flips to hard-fail (engine raises
-- ``WorkflowSnapshotMissingError`` when the field is empty), so any
-- residual rows must be drained or cancelled before that deploy.
--
-- No FK constraint enforced — the snapshot row outlives the run row
-- only by convention — coupling delete ordering at the schema level
-- would complicate run deletion.

ALTER TABLE "AIW_WORKFLOW_RUNS"
    ADD ("WORKFLOW_SNAPSHOT_ID" NVARCHAR(64) DEFAULT '' NOT NULL);
