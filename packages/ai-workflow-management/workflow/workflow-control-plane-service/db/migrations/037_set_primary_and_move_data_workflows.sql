-- Promote WORKFLOW_ID to the primary key and backfill the new ID column.
--
-- Guarded for the no-ledger runner: ADD PRIMARY KEY raises a non-swallowed
-- error when a primary key already exists on a re-run. Add it only when the
-- table currently has no primary key — paired with 036 (which drops only a
-- stale PK on a column other than WORKFLOW_ID), this leaves an already-correct
-- PK untouched, so the 036/037 pair does not drop-and-re-add the key on every
-- boot. The UPDATE is already idempotent via its WHERE guard.
DO BEGIN
    DECLARE pk_count INTEGER;

    SELECT COUNT(*) INTO pk_count
        FROM "SYS"."CONSTRAINTS"
        WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
          AND "TABLE_NAME" = 'AIW_WORKFLOWS'
          AND "IS_PRIMARY_KEY" = 'TRUE';
    IF :pk_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE "AIW_WORKFLOWS" ADD PRIMARY KEY ("WORKFLOW_ID")';
    END IF;

    UPDATE "AIW_WORKFLOWS" SET "ID" = "WORKFLOW_ID" WHERE "ID" IS NULL OR "ID" = '';
END;
