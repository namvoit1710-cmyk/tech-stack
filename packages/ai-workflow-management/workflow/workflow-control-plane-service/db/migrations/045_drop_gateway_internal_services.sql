-- Clean-break: the v1 single-endpoint gateway service registry (038) is
-- superseded by AIW_GATEWAY_APPS (043) + AIW_GATEWAY_FUNCTIONS (044). The
-- registry held no production rows. Guarded DROP so a clean boot, a full
-- re-run, and a resumed partial run all converge: the table is dropped only
-- while it still exists. DO BEGIN is one procedural statement.
DO BEGIN
    DECLARE tbl_count INTEGER;

    SELECT COUNT(*) INTO tbl_count
        FROM "SYS"."TABLES"
        WHERE "SCHEMA_NAME" = CURRENT_SCHEMA
          AND "TABLE_NAME" = 'AIW_GATEWAY_INTERNAL_SERVICES';

    IF :tbl_count > 0 THEN
        EXECUTE IMMEDIATE 'DROP TABLE "AIW_GATEWAY_INTERNAL_SERVICES"';
    END IF;
END;
