-- Migration: Add TIMEOUT_AT column to AIW_TASKS for child workflow timeout enforcement
-- This column stores the deadline timestamp for tracking tasks (WORKFLOW node type)
-- The sweeper process queries tasks WHERE TIMEOUT_AT <= NOW() AND STATUS = 'RUNNING'

ALTER TABLE "AIW_TASKS" ADD ("TIMEOUT_AT" NVARCHAR(64));

-- Index for efficient timeout sweeping: find tasks past their deadline
CREATE INDEX "IDX_AIW_TASKS_TIMEOUT" ON "AIW_TASKS" ("TIMEOUT_AT", "STATUS");
