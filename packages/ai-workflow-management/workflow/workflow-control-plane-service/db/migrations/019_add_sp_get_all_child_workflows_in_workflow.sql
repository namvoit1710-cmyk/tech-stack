CREATE OR REPLACE PROCEDURE "GET_ALL_CHILD_WORKFLOWS_IN_WORKFLOW" (
    IN IP_START_ID NVARCHAR(64)
)
LANGUAGE SQLSCRIPT 
READS SQL DATA AS
BEGIN
    -- 1. Initialize Table Variables (to keep the loop lightweight, we only track IDs)
    DECLARE T_ALL_IDS TABLE ("ID" NVARCHAR(64));
    DECLARE T_NEW_IDS TABLE ("ID" NVARCHAR(64));

    -- 2. Seed with the starting ID
    T_ALL_IDS = SELECT :IP_START_ID AS "ID" FROM DUMMY;
    T_NEW_IDS = SELECT "ID" FROM :T_ALL_IDS;

    -- 3. Loop while there are still new IDs to explore
    WHILE NOT IS_EMPTY(:T_NEW_IDS) DO
        
        -- Find nested workflow_ids from the NODES of the current layer
        T_FOUND_CHILDREN = 
            SELECT DISTINCT JT."NESTED_WF_ID" AS "ID"
            FROM "AIW_WORKFLOWS" W
            INNER JOIN :T_NEW_IDS N ON W."ID" = N."ID"
            CROSS JOIN JSON_TABLE(
                AIW_WORKFLOWS."NODES", '$[*]'
                COLUMNS ("NESTED_WF_ID" NVARCHAR(64) PATH '$.config.workflow_id')
            ) JT
            WHERE JT."NESTED_WF_ID" IS NOT NULL;

        -- Identify IDs we haven't seen before (prevents infinite loops)
        T_NEW_IDS = SELECT "ID" FROM :T_FOUND_CHILDREN 
                    EXCEPT 
                    SELECT "ID" FROM :T_ALL_IDS;

        -- Add the newly discovered IDs to the master list
        T_ALL_IDS = SELECT "ID" FROM :T_ALL_IDS 
                    UNION ALL
                    SELECT "ID" FROM :T_NEW_IDS;
                    
    END WHILE;

    -- 4. Join back to the main table to get ALL data for the IDs found
        SELECT W.ID,
        	W.NAME,
        	W.NODES
        FROM "AIW_WORKFLOWS" W
        INNER JOIN :T_ALL_IDS T ON W."ID" = T."ID";

END;