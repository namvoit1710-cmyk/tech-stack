-- SA-1734: node definitions carry a functional KIND and a user-set COLLECTION.
--
-- KIND (trigger/action/read/logic/human/transform/util) is deterministically
-- re-derivable on read, so legacy NULL rows self-heal in NodeDefinition.__post_init__
-- via default_kind. COLLECTION is user free text ("My Nodes", "Google", ...) and
-- MUST be persisted or a client's collection is lost and heals back to "My Nodes"
-- on the next read. Both columns are nullable (no server default) — the entity
-- coalesces NULL/"" to its heal value, mirroring the base_worker_id self-heal.
--
-- Idempotent: the ADDs are caught by the runner's "column already exists" codes
-- (308/327) on any reconciling re-run.

ALTER TABLE "AIW_NODE_DEFINITIONS" ADD ("KIND" NVARCHAR(32));
ALTER TABLE "AIW_NODE_DEFINITIONS" ADD ("COLLECTION" NVARCHAR(256));
