# code-worker — deploy prerequisites

Preconditions that must be true in the target env BEFORE deploy. Config values live in
`deployment.yml`; this documents the setup that config assumes. Keep current in the same PR.

## Service instances (must exist — run Provision Services)
- **None bound.** `services:` in `deployment.yml` is empty for both tiers — this worker binds
  no service instances. It runs sandboxed script execution (JavaScript/Python code nodes) and
  needs no HANA/messaging/logging binding of its own.

## Upstream services (must be deployed & reachable)
- **ai-workflow worker executor / registry** — via `REGISTRY_URL`
  (`…ai-workflow-worker-executor…`). This worker registers and receives tasks from the
  executor; it must be deployed and reachable or the worker gets no work. The executor also
  enforces `WORKER_MAX_CONCURRENT`/429-Retry-After back-pressure documented in `deployment.yml`.
- **file-service** — via `FILE_SERVICE_URL`. Needed for file-backed inputs/outputs and
  chunked streaming (`WORKER_CHUNKED_OUTPUT_ENABLED=true`).
- `PROXY_URL` is this worker's own public route (the executor calls back on it) — must resolve
  to this app after deploy.

## Notes on sizing (already in deployment.yml)
- `memory: 256M` is coupled to the code-node memory ceilings the worker derives at boot, and to
  `WORKER_MAX_CONCURRENT=2` / `CODE_NODE_MAX_CONCURRENT=1`. Change memory and concurrency
  together — the derived ceilings scale from the container quota. No action needed unless you
  intentionally resize.

## Verify after deploy
- Worker appears as registered/healthy against the executor (`REGISTRY_URL`).
- A trivial `code` node (e.g. return a value) executes and streams its result back.
