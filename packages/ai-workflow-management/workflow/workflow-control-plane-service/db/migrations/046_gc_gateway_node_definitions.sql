-- Clean break: gateway functions no longer auto-generate a workflow palette
-- NodeDefinition (gwfn-*). Functions are now invoked only via the autoform
-- gateway_call action step (function chosen by id through a searchable picker),
-- so the previously generated system-tenant nodes must be removed from the
-- palette. One-time GC. Idempotent: a re-run deletes nothing once they are gone.
DELETE FROM "AIW_NODE_DEFINITIONS"
    WHERE "TENANT_ID" = 'system'
      AND "ID" LIKE 'gwfn-%';
