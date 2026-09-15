# http-request-worker — deploy prerequisites

Preconditions that must be true in the target env BEFORE deploy. Config values live in
`deployment.yml`; this documents the setup that config assumes. Keep current in the same PR.

## Service instances (must exist — run Provision Services)
Bindings differ per tier (`services:` in `deployment.yml`):

- **dev:**
  - `simplemdg-logging` (cloud-logging / large).
  - `simplemdg-event-core-2` (enterprise-messaging / default) — SAP event mesh (`MESSAGING_MODE=sap`).
- **qas:**
  - `simplemdg-logging` (cloud-logging / large).
  - `wio-orchestrator-management-001` — per-env orchestrator/messaging instance (NOT in
    `required-services.yml`; must exist before deploy).

## Upstream services (must be deployed & reachable)
- **ai-workflow worker executor / registry** — via `REGISTRY_URL`
  (`…ai-workflow-worker-executor…`). Worker registers and receives tasks here. The executor's
  `TASK_TIMEOUT_MAX_SECONDS` (3600) − callback grace (30) bounds this worker's
  `HTTP_MAX_TIMEOUT_SECONDS=3000`; keep them consistent if the executor's timeouts change.
- **file-service** — via `FILE_SERVICE_URL`. Central to this worker: it streams large
  file-backed ARRAY request bodies (`WORKER_STREAM_FILE_BACKED_INPUTS=true`,
  `RESOLVE_MAX_INPUT_BYTES=64 MiB`), so file-service must be deployed and reachable.
- `PROXY_URL` is this worker's own public route (executor callback target) — must resolve after deploy.

## Runtime secrets (not in git — cf set-env)
- None set via `cf set-env`. Any auth for the outbound HTTP targets is a per-node input /
  vault-resolved credential, not app env.
- Operational flag (already set): `WORKER_STREAM_FILE_BACKED_INPUTS` can be flipped live for
  rollback (`cf set-env … false` + `cf restart`) — no redeploy needed.

## External systems
- The arbitrary HTTP endpoints a workflow points this worker at must be **reachable from the CF
  egress**. On-prem targets would need a BTP destination + Cloud Connector — none is configured
  here, so today this worker reaches public/CF-internal HTTP endpoints only.

## Notes on sizing (already in deployment.yml)
- `memory: 512M` is deliberately coupled to `RESOLVE_MAX_INPUT_BYTES` (64 MiB) — a manifest test
  fails if they move apart. Also note: tenant-1 (`solace`) reads the `dev` tier, so the dev row
  is effectively production. Do not resize casually.

## Verify after deploy
- Worker appears as registered/healthy against the executor (`REGISTRY_URL`).
- An `http_request` node POSTs a small body to a reachable endpoint and returns; then a
  file-backed array body streams through (proves file-service + streaming path).
