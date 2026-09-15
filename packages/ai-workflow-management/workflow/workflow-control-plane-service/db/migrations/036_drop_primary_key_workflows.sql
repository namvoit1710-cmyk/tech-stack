-- Drop the OLD primary key on AIW_WORKFLOWS so 037 can re-key it on WORKFLOW_ID.
--
-- The migration runner keeps no applied-ledger: it re-executes every statement
-- on each startup and relies on each migration being idempotent. A bare
-- ``ALTER TABLE "AIW_WORKFLOWS" DROP PRIMARY KEY`` raises HANA error 261
-- ("no primary key on table") whenever the PK is already absent — e.g. a
-- previous run dropped it here and failed before 037 re-added it. That error
-- is not in the runner's "already satisfied" skip set, so it aborts the whole
-- migration and the service falls back to the mock backend.
--
-- Drop only a STALE primary key — one whose column is not the new WORKFLOW_ID.
-- That makes the no-PK state a clean no-op (the failure this fixes) AND leaves
-- an already-correct PK in place, so this migration and 037 do not drop-and-
-- re-add the key on every boot (ADD/DROP PRIMARY KEY rebuilds the table's
-- primary index — not something to repeat each startup). ``DO BEGIN`` is
-- recognised by the runner as a single procedural statement.
DO BEGIN
    DECLARE stale_pk_count INTEGER;
    SELECT COUNT(*) INTO stale_pk_count
        FROM "SYS"."CONSTRAINTS"
        WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
          AND "TABLE_NAME" = 'AIW_WORKFLOWS'
          AND "IS_PRIMARY_KEY" = 'TRUE'
          AND "COLUMN_NAME" <> 'WORKFLOW_ID';
    IF :stale_pk_count > 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE "AIW_WORKFLOWS" DROP PRIMARY KEY';
    END IF;
END;
