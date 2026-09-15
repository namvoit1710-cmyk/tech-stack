# ai-workflow-control-plane — deploy prerequisites

Preconditions that must be true in the target env BEFORE deploy. Config values live in
`deployment.yml`; this documents the setup that config assumes. Keep current in the same PR.

This is the **control plane** (WCP) of the ai-workflow system: it holds run state, dispatches
tasks to the executor, and serves the gRPC hub the rest of the workflow stack addresses.

## Service instances (must exist — run Provision Services)
- `workflow-control-plane-db` (hana / hdi-shared) — dev tier; the control-plane state DB.
- `simplemdg-event-core-2` (enterprise-messaging / default) — Event Mesh (dev uses
  `TRANSPORT_MODE=event_mesh` for task dispatch + `workflow.results`; also feeds solace→tenant-1).
- qas tier binds `wio-workflow-control-plane-db`, `simplemdg-logging` (cloud-logging / large),
  and `wio-orchestrator-management-001` — the `wio-*` instances are env-specific and must
  pre-exist in the qas sub-account.

## HANA / HDI
- `workflow-control-plane-db` HDI container provisioned before deploy. `hdiService: dev`
  makes `@sap/hdi-deploy` apply migrations (from `db/hdi/`) at deploy — the app runs no DDL
  on dev (`HANA_RUN_MIGRATIONS=false`). qas self-migrates (`HANA_RUN_MIGRATIONS=true`) against
  its own instance.
- `EVENT_LOG_BACKEND=hana` and `WEBHOOK_BACKEND=hana` — run event log + webhooks persist to
  this schema; it must be reachable and migrated.

## Upstream services (must be deployed & reachable)
- worker-executor-service — via `WORKER_EXECUTION_URL` (http) **and**
  `WORKER_EXECUTION_GRPC_TARGET` = `ai-workflow-worker-executor-<tier>.apps.internal:50052`
  (internal gRPC). The executor must be deployed and its internal route mapped.
- file-service — via `FILE_SERVICE_URL`.
- schema-transform-service — via `SCHEMA_TRANSFORM_URL`.
- push-gateway — via `PUSH_GATEWAY_URL` (live run SSE updates to the frontend).
- credentials-management — via `CREDENTIALS_VAULT_URL` (SA-1335). dev points at
  `smdg-ai-dev-credentials-management`; **qas is empty** = vault dormant (resolver only fires
  for nodes carrying a `credential_ref`, so envs without it are unaffected until used).

## Internal routing
- **Exposes** a gRPC server on `GRPC_PORT=50051` (`GRPC_SERVER_ENABLED=true` in every mode, so
  the CF port health check passes and gRPC callers keep working even under event_mesh dispatch).
- **Consumes** the executor's internal gRPC route at `...apps.internal:50052` — that route
  must be mapped on the executor side.

## Runtime secrets (not in git — set as secure user-provided vars, NOT in deployment.yml)
- `SMARTSHEET_TOKEN` — System Admin secret for the Smartsheet event-trigger poller
  (`SMARTSHEET_POLLER_ENABLED=true`). Without it the poller cannot drain the Event Reporting API.

## Verify after deploy
- gRPC server is listening on 50051 (health check green) and the executor's
  `...apps.internal:50052` is reachable from this app.
- A run persists to `workflow-control-plane-db` (event log rows appear) and dispatches to the
  executor.
