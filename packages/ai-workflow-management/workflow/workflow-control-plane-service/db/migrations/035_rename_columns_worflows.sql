-- Restructure AIW_WORKFLOWS identity columns: the old PK column ID becomes
-- WORKFLOW_ID, and GROUP_ID becomes the new ID.
--
-- Guarded for the no-ledger runner: RENAME COLUMN is not idempotent — on a
-- re-run the source column is gone and/or the target already exists, raising
-- errors the runner does not swallow. Worse, an unguarded re-run of step 1
-- could rename the NEW ID (formerly GROUP_ID) into WORKFLOW_ID and corrupt the
-- mapping. Each rename fires only when its source is present and its target
-- absent, which also resumes a prior run that completed step 1 but not step 2.
DO BEGIN
    DECLARE has_workflow_id INTEGER;
    DECLARE has_group_id INTEGER;

    -- Step 1: ID -> WORKFLOW_ID. Skip once WORKFLOW_ID exists so a re-run never
    -- touches the new ID column.
    SELECT COUNT(*) INTO has_workflow_id
        FROM "SYS"."TABLE_COLUMNS"
        WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
          AND "TABLE_NAME" = 'AIW_WORKFLOWS'
          AND "COLUMN_NAME" = 'WORKFLOW_ID';
    IF :has_workflow_id = 0 THEN
        EXECUTE IMMEDIATE 'RENAME COLUMN "AIW_WORKFLOWS"."ID" TO "WORKFLOW_ID"';
    END IF;

    -- Step 2: GROUP_ID -> ID. Skip once GROUP_ID is gone.
    SELECT COUNT(*) INTO has_group_id
        FROM "SYS"."TABLE_COLUMNS"
        WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
          AND "TABLE_NAME" = 'AIW_WORKFLOWS'
          AND "COLUMN_NAME" = 'GROUP_ID';
    IF :has_group_id > 0 THEN
        EXECUTE IMMEDIATE 'RENAME COLUMN "AIW_WORKFLOWS"."GROUP_ID" TO "ID"';
    END IF;
END;
