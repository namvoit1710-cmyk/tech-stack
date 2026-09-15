# worker-ms-teams-worker — deploy prerequisites

Preconditions that must be true in the target env BEFORE deploy. Config values live in
`deployment.yml`; this documents the setup that config assumes. Keep current in the same PR.

A `WORKER_TYPE=ms_teams` worker: it registers with the executor and posts to Microsoft Teams.
No DB of its own. `deployment.yml` binds no service instances (empty `services:` block).

## Upstream services (must be deployed & reachable)
- worker-executor-service — via `REGISTRY_URL` (the worker registers with the executor; if it's
  down the worker cannot join the fleet).
- file-service — via `FILE_SERVICE_URL`.

## Runtime secrets (not in git — cf set-env)
- `deployment.yml` carries **no** Teams webhook/credential. A worker that posts to Microsoft
  Teams needs an outbound Teams **incoming-webhook URL** (or equivalent bot credential) supplied
  at runtime — set it as a secure user-provided/`cf set-env` variable, never committed. Confirm
  the exact env var name the worker code reads before deploy; deployment.yml does not name it.

## External connectivity
- Egress to Microsoft Teams (webhook host) must be permitted from this CF space.

## Verify after deploy
- Worker appears registered at the executor (visible in the workflow fleet).
- A test `ms_teams` node delivers a message to the target Teams channel (webhook wired).
