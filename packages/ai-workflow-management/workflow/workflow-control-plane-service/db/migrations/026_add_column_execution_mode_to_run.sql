-- Add execution_mode column for manual trigger mode support
-- Values: 'auto' (default) | 'manual'
ALTER TABLE "AIW_WORKFLOW_RUNS" ADD ("EXECUTION_MODE" NVARCHAR(20) DEFAULT 'auto')