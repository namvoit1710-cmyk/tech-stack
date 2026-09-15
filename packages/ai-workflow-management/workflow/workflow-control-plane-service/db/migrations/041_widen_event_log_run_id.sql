-- Migration: Widen event-log + snapshot RUN_ID to fit composite child run IDs
--
-- Migration 018 widened AIW_TASKS.RUN_ID and AIW_WORKFLOW_RUNS.PARENT_RUN_ID to
-- NVARCHAR(256) so deterministic child-workflow run ids fit. Those ids now look
-- like "{parent_run_id}_{node_id}_{generation}" (~75 chars), e.g.
--   316e9fbe-4b14-498f-9f9c-6d6b4ec29464_167a3408-f220-4bf1-8e04-0881460dc5d1_0
--
-- The event-log table (025) and run-snapshot table (028) were created AFTER 018
-- with RUN_ID NVARCHAR(64), so they were never widened. Every child-run event
-- append therefore fails with HANA error 274 ("inserted value too large for
-- column: ... RUN_ID"), the child's projector view stays empty, and child
-- sub-workflow node outputs never propagate back to the parent WORKFLOW node.

ALTER TABLE "AIW_EVENT_LOG" ALTER ("RUN_ID" NVARCHAR(256));

-- AIW_RUN_SNAPSHOTS carries the identical defect (created post-018). Not
-- exercised while SNAPSHOT_MODE=off, but widened here so the snapshot/cold-start
-- replay path doesn't re-discover this the day it is enabled.
ALTER TABLE "AIW_RUN_SNAPSHOTS" ALTER ("RUN_ID" NVARCHAR(256));
