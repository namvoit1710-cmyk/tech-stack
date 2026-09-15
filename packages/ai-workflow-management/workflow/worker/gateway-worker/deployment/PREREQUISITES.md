# gateway-worker — deploy prerequisites

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
- **ai-workflow control-plane** — via `CONTROL_PLANE_URL`
  (`…ai-workflow-control-plane…`). Unique to this worker: the gateway node talks to the
  workflow control plane, which must be deployed and reachable in this env.
- **ai-workflow worker executor / registry** — via `REGISTRY_URL`
  (`…ai-workflow-worker-executor…`). Worker registers and receives tasks here.
- **file-service** — via `FILE_SERVICE_URL`. For file-backed inputs/outputs
  (`WORKER_CHUNKED_OUTPUT_ENABLED=true`).
- `PROXY_URL` is this worker's own public route (executor callback target) — must resolve after deploy.

## Runtime secrets (not in git — cf set-env)
- None set via `cf set-env`.

## Verify after deploy
- Worker appears as registered/healthy against the executor (`REGISTRY_URL`).
- A `gateway` node reaches the control plane (`CONTROL_PLANE_URL`) end-to-end.
