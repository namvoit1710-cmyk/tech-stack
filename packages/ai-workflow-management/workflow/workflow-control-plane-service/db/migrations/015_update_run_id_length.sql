-- Migration: Extend workflow run ID column to support composite child run IDs
-- Child workflow runs use predefined IDs in format "{parent_run_id}-{node_id}"

ALTER TABLE "AIW_WORKFLOW_RUNS" ALTER ("ID" NVARCHAR(256));
