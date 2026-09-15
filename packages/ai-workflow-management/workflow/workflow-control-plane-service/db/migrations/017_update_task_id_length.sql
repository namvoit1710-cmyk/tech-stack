-- Migration: Extend task ID column to support composite child run task IDs
-- Task IDs are formatted as "{run_id}-{node_id}" and child run IDs can be longer

ALTER TABLE "AIW_TASKS" ALTER ("ID" NVARCHAR(256));
