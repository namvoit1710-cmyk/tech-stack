-- Reverses migration 014: timeout-based child run sweeping has been removed.
-- The control plane no longer stamps deadlines on tracking tasks or runs a
-- background sweeper. Child runs stay in their PENDING/RUNNING state so the
-- user can re-open and resume them at any time.
--
-- Guarded for the no-ledger runner (it re-executes every migration on each
-- boot and only swallows duplicate-object errors): a bare DROP of an
-- already-absent index or column raises codes the runner does NOT skip
-- (e.g. 261 "invalid index name"), which aborts the whole migration. Drop
-- only what still exists, so re-runs are clean no-ops. ``DO BEGIN`` is
-- recognised by the runner as a single procedural statement.
DO BEGIN
    DECLARE idx_count INTEGER;
    DECLARE col_count INTEGER;

    SELECT COUNT(*) INTO idx_count
        FROM "SYS"."INDEXES"
        WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
          AND "TABLE_NAME" = 'AIW_TASKS'
          AND "INDEX_NAME" = 'IDX_AIW_TASKS_TIMEOUT';
    IF :idx_count > 0 THEN
        EXECUTE IMMEDIATE 'DROP INDEX "IDX_AIW_TASKS_TIMEOUT"';
    END IF;

    SELECT COUNT(*) INTO col_count
        FROM "SYS"."TABLE_COLUMNS"
        WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
          AND "TABLE_NAME" = 'AIW_TASKS'
          AND "COLUMN_NAME" = 'TIMEOUT_AT';
    IF :col_count > 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE "AIW_TASKS" DROP ("TIMEOUT_AT")';
    END IF;
END;
