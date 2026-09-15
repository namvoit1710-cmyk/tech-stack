-- Phase M.2.1: optimistic-concurrency version column for WorkflowRun.
-- HanaRunRepository.save increments this on every successful write and
-- uses WHERE VERSION = :expected as the CAS predicate for UPDATEs.
-- Existing rows migrate transparently at DEFAULT 0, and the first
-- M.2.1 save bumps them to 1.
ALTER TABLE "AIW_WORKFLOW_RUNS" ADD ("VERSION" INTEGER DEFAULT 0 NOT NULL)
