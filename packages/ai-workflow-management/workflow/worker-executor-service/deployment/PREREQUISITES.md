# ai-workflow-worker-executor — deploy prerequisites

Preconditions that must be true in the target env BEFORE deploy. Config values live in
`deployment.yml`; this documents the setup that config assumes. Keep current in the same PR.

This is the **executor** the control plane dispatches tasks to; it is the classic internal-gRPC
hub that the WCP addresses at `...apps.internal:50052`.

## Service instances (must exist — run Provision Services)
- `workflow-control-plane-db` (hana / hdi-shared) — dev tier control-plane DB.
- `simplemdg-event-core-2` (enterprise-messaging / default) — Event Mesh. dev runs
  `TRANSPORT_MODE=event_mesh`: results flow back to WCP over `workflow.results` and the
  synchronous HTTP result reporter is disabled. qas keeps `TRANSPORT_MODE=http`.
- qas tier binds `wio-workflow-control-plane-db`, `simplemdg-logging` (cloud-logging / large),
  and `wio-orchestrator-management-001` — the `wio-*` instances are env-specific and must
  pre-exist in the qas sub-account.

## Upstream services (must be deployed & reachable)
- workflow-control-plane-service — via `WORKFLOW_CONTROL_PLANE_URL`. In event_mesh mode results
  are also returned over the Event Mesh; the URL must still resolve for control interactions.
- file-service — via `FILE_SERVICE_URL`.

## Internal routing
- **Exposes** an internal gRPC route consumed by the control plane at
  `ai-workflow-worker-executor-<tier>.apps.internal:50052`. That internal route must be mapped
  (CF internal domain) so WCP's `WORKER_EXECUTION_GRPC_TARGET` resolves.
- Also reachable over http at `SERVICE_PORT=8004`.

## Sizing note (not a prereq, but load-bearing)
- `MAX_CONCURRENT_TASKS` (dev 300 / qas 100) is the global in-flight cap; memory is sized to it
  (dev 1024M). `TASK_TIMEOUT_OVERRIDES` keys are `WORKER_TYPE` strings (e.g. `agent`, `code`),
  NOT CF app names — a wrong key silently falls back to the 120s default.

## Verify after deploy
- WCP can reach `ai-workflow-worker-executor-<tier>.apps.internal:50052` (dispatch succeeds).
- On dev, task results appear back at WCP over `workflow.results` (event_mesh); on qas, over http.
