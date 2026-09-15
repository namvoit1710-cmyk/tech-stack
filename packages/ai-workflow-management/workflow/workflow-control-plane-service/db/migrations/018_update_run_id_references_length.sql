-- Migration: Extend RUN_ID reference columns to support composite child run IDs
-- Child workflow runs use predefined IDs in format "{parent_run_id}-{node_id}"
-- which can exceed the original NVARCHAR(64) limit.

ALTER TABLE "AIW_TASKS" ALTER ("RUN_ID" NVARCHAR(256));

ALTER TABLE "AIW_WORKFLOW_RUNS" ALTER ("PARENT_RUN_ID" NVARCHAR(256));
