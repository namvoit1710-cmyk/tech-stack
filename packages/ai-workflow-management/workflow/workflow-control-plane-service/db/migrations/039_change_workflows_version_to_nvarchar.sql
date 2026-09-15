-- Migration: AIW_WORKFLOWS.VERSION becomes a UUID string (NVARCHAR(64)) and the
-- run / snapshot FK columns widen to match. New workflow versions are assigned
-- as str(uuid.uuid4()) in UpdateWorkflowUseCase (36 chars). Existing values are
-- preserved as their string form (TO_NVARCHAR) so existing run / snapshot rows
-- that reference '1', '2', ... still resolve. A swap-column form is used (vs a
-- single ALTER) so it works on HANA versions that reject an implicit
-- INTEGER -> NVARCHAR conversion on a populated column.
--
-- Guarded for the no-ledger runner: the original swap (drop index, add
-- VERSION_NEW, copy, drop VERSION, rename, recreate index) is order-dependent
-- and aborts on a partial-failure re-run because the resulting "object missing"
-- errors are not swallowed. Each step below fires only when its precondition
-- holds, and the whole swap is skipped once VERSION is already NVARCHAR(>=64)
-- with no staging column left — so a clean boot, a full re-run, and a resumed
-- partial run all converge on the same final shape without per-boot churn.
-- The column type is detected (not assumed INTEGER) because older lineages
-- left VERSION as NVARCHAR(32). ``DO BEGIN`` is one procedural statement.
DO BEGIN
    DECLARE v_type    NVARCHAR(64);
    DECLARE v_len     INTEGER;
    DECLARE has_new   INTEGER;
    DECLARE has_old   INTEGER;
    DECLARE idx_count INTEGER;
    DECLARE col_len   INTEGER;

    SELECT MAX("DATA_TYPE_NAME"), MAX("LENGTH") INTO v_type, v_len
        FROM "SYS"."TABLE_COLUMNS"
        WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
          AND "TABLE_NAME" = 'AIW_WORKFLOWS'
          AND "COLUMN_NAME" = 'VERSION';
    SELECT COUNT(*) INTO has_new
        FROM "SYS"."TABLE_COLUMNS"
        WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
          AND "TABLE_NAME" = 'AIW_WORKFLOWS'
          AND "COLUMN_NAME" = 'VERSION_NEW';

    -- Run the swap unless VERSION is already the final NVARCHAR(>=64) with no
    -- staging column left over.
    IF :has_new > 0 OR :v_type IS NULL OR :v_type <> 'NVARCHAR' OR :v_len < 64 THEN
        -- Drop the composite index if present (recreated at the end).
        SELECT COUNT(*) INTO idx_count
            FROM "SYS"."INDEXES"
            WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
              AND "TABLE_NAME" = 'AIW_WORKFLOWS'
              AND "INDEX_NAME" = 'IDX_AIW_WORKFLOWS_GROUP_VERSION';
        IF :idx_count > 0 THEN
            EXECUTE IMMEDIATE 'DROP INDEX "IDX_AIW_WORKFLOWS_GROUP_VERSION"';
        END IF;

        -- Stage VERSION_NEW.
        SELECT COUNT(*) INTO has_new
            FROM "SYS"."TABLE_COLUMNS"
            WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
              AND "TABLE_NAME" = 'AIW_WORKFLOWS'
              AND "COLUMN_NAME" = 'VERSION_NEW';
        IF :has_new = 0 THEN
            EXECUTE IMMEDIATE 'ALTER TABLE "AIW_WORKFLOWS" ADD ("VERSION_NEW" NVARCHAR(64) DEFAULT '''')';
        END IF;

        -- Copy values across and drop the old column while it still exists.
        SELECT COUNT(*) INTO has_old
            FROM "SYS"."TABLE_COLUMNS"
            WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
              AND "TABLE_NAME" = 'AIW_WORKFLOWS'
              AND "COLUMN_NAME" = 'VERSION';
        IF :has_old > 0 THEN
            EXECUTE IMMEDIATE 'UPDATE "AIW_WORKFLOWS" SET "VERSION_NEW" = TO_NVARCHAR("VERSION")';
            EXECUTE IMMEDIATE 'ALTER TABLE "AIW_WORKFLOWS" DROP ("VERSION")';
        END IF;

        -- Promote VERSION_NEW to VERSION.
        SELECT COUNT(*) INTO has_old
            FROM "SYS"."TABLE_COLUMNS"
            WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
              AND "TABLE_NAME" = 'AIW_WORKFLOWS'
              AND "COLUMN_NAME" = 'VERSION';
        IF :has_old = 0 THEN
            EXECUTE IMMEDIATE 'RENAME COLUMN "AIW_WORKFLOWS"."VERSION_NEW" TO "VERSION"';
        END IF;
    END IF;

    -- Recreate the composite index on the final (ID, VERSION) shape if missing.
    SELECT COUNT(*) INTO idx_count
        FROM "SYS"."INDEXES"
        WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
          AND "TABLE_NAME" = 'AIW_WORKFLOWS'
          AND "INDEX_NAME" = 'IDX_AIW_WORKFLOWS_GROUP_VERSION';
    IF :idx_count = 0 THEN
        EXECUTE IMMEDIATE 'CREATE INDEX "IDX_AIW_WORKFLOWS_GROUP_VERSION" ON "AIW_WORKFLOWS" ("ID", "VERSION" DESC)';
    END IF;

    -- Widen run / snapshot FK columns to fit a 36-char UUID (only when narrower).
    SELECT MAX("LENGTH") INTO col_len
        FROM "SYS"."TABLE_COLUMNS"
        WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
          AND "TABLE_NAME" = 'AIW_WORKFLOW_RUNS'
          AND "COLUMN_NAME" = 'WORKFLOW_VERSION';
    IF :col_len IS NOT NULL AND :col_len < 64 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE "AIW_WORKFLOW_RUNS" ALTER ("WORKFLOW_VERSION" NVARCHAR(64))';
    END IF;

    SELECT MAX("LENGTH") INTO col_len
        FROM "SYS"."TABLE_COLUMNS"
        WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
          AND "TABLE_NAME" = 'AIW_WORKFLOW_SNAPSHOTS'
          AND "COLUMN_NAME" = 'WORKFLOW_VERSION';
    IF :col_len IS NOT NULL AND :col_len < 64 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE "AIW_WORKFLOW_SNAPSHOTS" ALTER ("WORKFLOW_VERSION" NVARCHAR(64))';
    END IF;
END;
