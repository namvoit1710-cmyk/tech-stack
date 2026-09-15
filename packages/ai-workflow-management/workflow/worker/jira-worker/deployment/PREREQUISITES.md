# jira-worker — deploy prerequisites

Preconditions that must be true in the target env BEFORE deploy. Config values live in
`deployment.yml`; this documents the setup that config assumes. Keep current in the same PR.

## Service instances (must exist — run Provision Services)
- **None bound.** `services:` in `deployment.yml` is empty for both tiers. This worker binds no
  service instances of its own.

## Upstream services (must be deployed & reachable)
- **ai-workflow worker executor / registry** — via `REGISTRY_URL`
  (`…ai-workflow-worker-executor…`). Worker registers and receives tasks here.
- **file-service** — via `FILE_SERVICE_URL`. For file-backed inputs/outputs
  (`WORKER_CHUNKED_OUTPUT_ENABLED=true`), e.g. Jira attachments.
- `PROXY_URL` is this worker's own public route (executor callback target) — must resolve after deploy.
  Note this worker listens on `SERVER_PORT=38002` (not the usual 35000).

## Runtime secrets (not in git — cf set-env)
- None set via `cf set-env`. Jira auth is **not** app env — every node shares the vault
  credential `jira-api-token` (provider JIRA, kind API_KEY), resolved and injected per task by
  the control plane. The credential blob carries the site domain + email + API token (HTTP
  Basic). Ensure `jira-api-token` exists in the vault the control plane serves.

## External systems
- The target **Jira site** (Cloud/Server URL from the credential's `domain`/`baseUrl`) must be
  **reachable from the CF egress** and the API token valid for the operations used
  (create/get/update issues, comments, transitions, attachments, etc.).

## Verify after deploy
- Worker appears as registered/healthy against the executor (`REGISTRY_URL`).
- A `jira_myself` (Test / Whoami) node succeeds against the target site (proves vault credential
  + egress), then a real operation (e.g. `jira_search`) runs.
