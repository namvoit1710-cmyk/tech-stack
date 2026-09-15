CREATE OR REPLACE PROCEDURE "BACKFILL_WORKFLOW_RELATIONS"()
LANGUAGE SQLSCRIPT
SQL SECURITY INVOKER AS
BEGIN
    DECLARE V_ID NVARCHAR(64);
    DECLARE V_NODES NCLOB;
    DECLARE CURSOR C_WORKFLOWS FOR
        SELECT "ID", "NODES" FROM "AIW_WORKFLOWS" WHERE "NODES" IS NOT NULL AND "ID" IS NOT NULL;

    FOR wf_row AS C_WORKFLOWS DO
        V_ID    = wf_row."ID";
        V_NODES = wf_row."NODES";

        INSERT INTO "AIW_WORKFLOW_RELATIONS" ("PARENT_WORKFLOW_ID", "CHILD_WORKFLOW_ID", "NODE_ID")
            SELECT
                :V_ID              AS "PARENT_WORKFLOW_ID",
                JT."NESTED_WF_ID"  AS "CHILD_WORKFLOW_ID",
                JT."NODE_ID"       AS "NODE_ID"
            FROM JSON_TABLE(
                :V_NODES, '$[*]'
                COLUMNS (
                    "NODE_ID"       NVARCHAR(128) PATH '$.id',
                    "NESTED_WF_ID"  NVARCHAR(64)  PATH '$.config.workflow_id'
                )
            ) JT
            WHERE JT."NESTED_WF_ID" IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM "AIW_WORKFLOW_RELATIONS" R
                  WHERE R."PARENT_WORKFLOW_ID" = :V_ID
                    AND R."NODE_ID" = JT."NODE_ID"
              );
    END FOR;
END;
