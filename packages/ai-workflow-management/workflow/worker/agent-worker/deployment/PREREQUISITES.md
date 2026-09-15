# agent-worker — deploy prerequisites

Preconditions that must be true in the target env BEFORE deploy. Config values live in
`deployment.yml`; this documents the setup that config assumes. Keep current in the same PR.

## Service instances (must exist — run Provision Services)
Bindings differ per tier (`services:` in `deployment.yml`):

- **dev:**
  - `workflow-control-plane-db` (hana / hdi-shared) — the workflow control-plane HANA container.
  - `simplemdg-event-core-2` (enterprise-messaging / default) — the SAP event mesh this worker uses (`MESSAGING_MODE=sap`).
- **qas:**
  - `wio-workflow-control-plane-db` — per-env control-plane HANA container (NOT in `required-services.yml`; must be provisioned in this sub-account first).
  - `simplemdg-logging` (cloud-logging / large).
  - `wio-orchestrator-management-001` — per-env orchestrator/messaging instance (NOT in `required-services.yml`; must exist before deploy).

## Upstream services (must be deployed & reachable)
- **ai-workflow worker executor / registry** — via `REGISTRY_URL`
  (`…ai-workflow-worker-executor…`). This worker registers itself and receives tasks
  from the executor; it must be deployed and its route reachable, or the worker never
  gets work.
- **file-service** — via `FILE_SERVICE_URL`. Needed for file-backed inputs/outputs
  (`WORKER_CHUNKED_OUTPUT_ENABLED=true`).
- `PROXY_URL` is this worker's own public route (the executor calls back on it) — the route
  must resolve to this app after deploy.

## Runtime secrets (not in git — cf set-env)
- None set via `cf set-env`. The OpenAI credential this worker uses for chat completions is
  **not** an app env var — it is a vault credential resolved and injected per-task by the
  control plane at runtime. Ensure that credential exists in the vault the control plane serves.

## External systems
- **OpenAI (or configured AI provider)** — reachable from the CF egress. The worker integrates
  with OpenAI for chat completions; the API key arrives via the control-plane vault, not env.

## Verify after deploy
- Worker appears as registered/healthy against the executor (`REGISTRY_URL`).
- A test `agent` node runs a chat completion end-to-end (proves vault credential + provider egress).
