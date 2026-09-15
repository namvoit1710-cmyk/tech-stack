-- G-guard: coarse per-run generation counter. Bumped only on semantic
-- "new execution" events (test-run reset, future restart). Tasks carry
-- a ``RUN_GENERATION`` stamp from task creation time. Callback handlers
-- reject stamps older than the current run.
-- Existing rows migrate transparently at DEFAULT 0 — which is a valid
-- generation, not a sentinel. Re-executing an existing run bumps to 1.
ALTER TABLE "AIW_WORKFLOW_RUNS" ADD ("GENERATION" INTEGER DEFAULT 0 NOT NULL)
