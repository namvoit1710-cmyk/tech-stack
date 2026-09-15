# worker-mapping-data-worker — deploy prerequisites

Preconditions that must be true in the target env BEFORE deploy. Config values live in
`deployment.yml`; this documents the setup that config assumes. Keep current in the same PR.

A `WORKER_TYPE=mapping_data` worker: it registers with the executor and pulls/pushes data via
file-service. No DB of its own.

## Service instances (must exist — run Provision Services)
- `simplemdg-logging` (cloud-logging / large) — bound on both tiers.
- `simplemdg-event-core-2` (enterprise-messaging / default) — dev tier (Event Mesh).
- qas tier binds `wio-workflow-control-plane-db` and `wio-orchestrator-management-001` —
  env-specific `wio-*` instances that must pre-exist in the qas sub-account.

## Upstream services (must be deployed & reachable)
- worker-executor-service — via `REGISTRY_URL` (the worker registers with the executor; if it's
  down the worker cannot join the fleet). Its `WORKER_TYPE=mapping_data` must be within the
  executor's known types.
- file-service — via `FILE_SERVICE_URL` (reads inputs, writes chunked output;
  `WORKER_CHUNKED_OUTPUT_ENABLED=true`).

## Verify after deploy
- Worker appears registered at the executor (visible in the workflow fleet).
- A `mapping_data` node runs end-to-end (input read + chunked output written via file-service).
