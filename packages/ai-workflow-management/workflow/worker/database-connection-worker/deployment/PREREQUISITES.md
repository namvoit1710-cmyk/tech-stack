# database-connection-worker — deploy prerequisites

Preconditions that must be true in the target env BEFORE deploy. Config values live in
`deployment.yml`; this documents the setup that config assumes. Keep current in the same PR.

## Service instances (must exist — run Provision Services)
Bindings differ per tier (`services:` in `deployment.yml`):

- **dev:**
  - `workflow-control-plane-db` (hana / hdi-shared) — the workflow control-plane HANA container.
  - `simplemdg-event-core-2` (enterprise-messaging / default) — SAP event mesh (`MESSAGING_MODE=sap`).
- **qas:**
  - `wio-workflow-control-plane-db` — per-env control-plane HANA container (NOT in
    `required-services.yml`; provision in this sub-account first).
  - `simplemdg-logging` (cloud-logging / large).
  - `wio-orchestrator-management-001` — per-env orchestrator/messaging instance (NOT in
    `required-services.yml`; must exist before deploy).

## Upstream services (must be deployed & reachable)
- **ai-workflow worker executor / registry** — via `REGISTRY_URL`
  (`…ai-workflow-worker-executor…`). Worker registers and receives tasks here.
- **file-service** — via `FILE_SERVICE_URL`. For file-backed inputs/outputs
  (`WORKER_CHUNKED_OUTPUT_ENABLED=true`).
- `PROXY_URL` is this worker's own public route (executor callback target) — must resolve after deploy.

## Runtime secrets (not in git — cf set-env)
- None set via `cf set-env`. The worker "handles database connections and operations," but the
  **target-database credentials are per-node inputs / vault-resolved at runtime**, not app env.
  The bound `workflow-control-plane-db` (dev) is the control-plane's own HANA, not the tenant
  databases this worker connects to on behalf of a workflow.

## External systems
- Any target database a workflow points this worker at must be **network-reachable from the CF
  egress** (and its credentials present wherever the control plane resolves node credentials).

## Verify after deploy
- Worker appears as registered/healthy against the executor (`REGISTRY_URL`).
- A `database-connection` node opens a connection to a reachable test DB and returns.
