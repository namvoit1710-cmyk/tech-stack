-- Migration: Widen AIW_TASKS.IDEMPOTENCY_KEY to fit the composite task.id.
--
-- task.id is built as "{run.id}-{node.id}{suffix}" where run.id was widened
-- to NVARCHAR(1024) in migration 026 to support deeply-nested child
-- workflows. idempotency_key is then derived as
--   "{task.id}-attempt-{n}"  (TaskFactory)
--   "{task.id}-rerun-{ts}"   (rerun_task)
--   "{task.id}-edit-{ts}"    (edit_node_input)
-- Adding ~40 chars to a 1024-char task.id overflows the old NVARCHAR(256)
-- cap (set in migration 004) and HANA rejects the insert with error 274
-- "inserted value too large for column".
--
-- NVARCHAR(1280) covers a 1024-char task.id plus the longest known suffix
-- ("-edit-{ISO timestamp}" ~= 40 chars) with margin. ALTER ... ALTER on
-- NVARCHAR is non-destructive: existing data is preserved and the column
-- index remains valid.

ALTER TABLE "AIW_TASKS" ALTER ("IDEMPOTENCY_KEY" NVARCHAR(1280));
