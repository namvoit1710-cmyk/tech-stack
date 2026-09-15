-- Migration: Widen run and task ID columns to handle deeply-nested child workflows.
--
-- Composite run IDs encode the full ancestor chain as
--   {root_uuid}_{child_uuid}_{timestamp}_{grandchild_uuid}_{timestamp}_...
-- Each nesting level adds ~50 chars (UUID + timestamp + separator). The
-- current NVARCHAR(256) cap (set by migrations 015, 017, 018) holds up to
-- ~4 nesting levels. Production runs nested 5+ levels deep are now
-- producing 277-char IDs that overflow the column, e.g. HANA error 274
-- "inserted value too large for column" when saving entry tasks for the
-- innermost child workflow.
--
-- Widening to NVARCHAR(1024) gives headroom for ~20 nesting levels. ALTER
-- ... ALTER on NVARCHAR is non-destructive: data is preserved and indices
-- on these columns remain valid. NVARCHAR storage in HANA is
-- variable-length, so wider columns do not waste space on shorter values.
--
-- IMPORTANT for future authors: the migration runner splits files on raw
-- semicolons without respecting SQL comments. A statement-terminator
-- character inside a comment line will cut the next ALTER in half and HANA
-- rejects it with error 257.

ALTER TABLE "AIW_TASKS" ALTER ("ID" NVARCHAR(1024));
ALTER TABLE "AIW_TASKS" ALTER ("RUN_ID" NVARCHAR(1024));

ALTER TABLE "AIW_WORKFLOW_RUNS" ALTER ("ID" NVARCHAR(1024));
ALTER TABLE "AIW_WORKFLOW_RUNS" ALTER ("PARENT_RUN_ID" NVARCHAR(1024));
ALTER TABLE "AIW_WORKFLOW_RUNS" ALTER ("ROOT_RUN_ID" NVARCHAR(1024));
