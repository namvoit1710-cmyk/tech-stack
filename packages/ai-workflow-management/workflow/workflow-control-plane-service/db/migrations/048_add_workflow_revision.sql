-- Workflow REVISION — durable "which version row is current" key.
--
-- A logical workflow ("ID") holds many version rows (one per "WORKFLOW_ID").
-- "current" used to be resolved with ORDER BY UPDATED_AT DESC, VERSION DESC,
-- which returned a STALE row whenever UPDATED_AT tied (microsecond collision /
-- coarse clock) or skewed across Cloud Run instances — VERSION is a random uuid,
-- so it was a meaningless tiebreak. REVISION is a monotonic per-ID counter set
-- on every save (max(REVISION)+1), so the most recently saved row always has the
-- highest REVISION. get_by_id / get_by_latest_publish_version / list_paged now
-- order by REVISION.
--
-- Idempotent: the ADD is caught by the runner's "column exists" codes (308/327)
-- on any reconciling re-run, and the backfill only touches rows not yet stamped
-- (REVISION IS NULL), so re-running it is a no-op.

ALTER TABLE "AIW_WORKFLOWS" ADD ("REVISION" BIGINT);

MERGE INTO "AIW_WORKFLOWS" t
USING (
    SELECT "WORKFLOW_ID",
           ROW_NUMBER() OVER (
               PARTITION BY "ID"
               ORDER BY "UPDATED_AT" ASC, "VERSION" ASC
           ) AS rn
    FROM "AIW_WORKFLOWS"
) s
ON t."WORKFLOW_ID" = s."WORKFLOW_ID"
WHEN MATCHED AND t."REVISION" IS NULL THEN UPDATE SET t."REVISION" = s.rn;
