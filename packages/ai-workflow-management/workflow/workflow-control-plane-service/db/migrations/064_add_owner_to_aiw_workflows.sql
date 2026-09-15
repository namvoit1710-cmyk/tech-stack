-- Workflow owner (SA-2571): a reassignable owner for a workflow. Defaults to the
-- creator on create; changed via PUT /workflows/{id}/owner. Stored as the owner's
-- id (Keycloak ``sub``), resolved to a display name via AIW_USER.
--
-- CREATED_BY / UPDATED_BY already exist (migration 033) but were never persisted
-- by the workflow repo — SA-2571 wires them through the save/read path too.

ALTER TABLE "AIW_WORKFLOWS" ADD ("OWNER" NVARCHAR(256) DEFAULT NULL);
