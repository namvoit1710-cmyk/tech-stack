-- Run attribution (SA-2558): who triggered this run. Derived server-side from
-- the verified BFF session / Bearer JWT at run creation (never from the request
-- body ``payload.user_id``), so attribution cannot be spoofed. Stored as the
-- caller's best display label captured at trigger time (name -> email -> id),
-- or the reserved ``system`` sentinel for automated / M2M / schedule-fired runs.
--
-- Nullable + no backfill: historical runs stay NULL and the Execution History
-- tab renders them as a neutral "System" chip.

ALTER TABLE "AIW_WORKFLOW_RUNS" ADD ("TRIGGERED_BY" NVARCHAR(256) DEFAULT NULL)
