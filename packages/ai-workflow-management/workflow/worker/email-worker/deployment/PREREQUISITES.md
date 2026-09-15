# email-worker — deploy prerequisites

Preconditions that must be true in the target env BEFORE deploy. Config values live in
`deployment.yml`; this documents the setup that config assumes. Keep current in the same PR.

## Service instances (must exist — run Provision Services)
- **None bound.** `services:` in `deployment.yml` is empty for both tiers. This worker binds no
  service instances of its own.

## Upstream services (must be deployed & reachable)
- **ai-workflow worker executor / registry** — via `REGISTRY_URL`
  (`…ai-workflow-worker-executor…`). Worker registers and receives tasks here.
- **file-service** — via `FILE_SERVICE_URL`. For file-backed inputs/outputs
  (`WORKER_CHUNKED_OUTPUT_ENABLED=true`), e.g. attachments.
- `PROXY_URL` is this worker's own public route (executor callback target) — must resolve after deploy.
  Note this worker listens on `SERVER_PORT=38001` (not the usual 35000).

## Runtime secrets (not in git — cf set-env)
- None set via `cf set-env`. SMTP username/password and the Microsoft Graph OAuth2 client
  credentials are **not** app env — the client reads them only from the vault-resolved
  credential the control plane injects per task (`SmtpEmailClient` docstring: "Credentials …
  come from the vault-resolved credential — this client never reads them from anywhere else").
  Ensure the SMTP / Graph credential exists in the vault the control plane serves.

## External systems
- **SMTP relay** (e.g. `smtp.office365.com`) and/or **Microsoft Graph `sendMail`** endpoint must
  be **reachable from the CF egress**. The worker sends via an SMTP relay or Graph; the
  destination host, port, and TLS mode are per-node inputs, credentials come from the vault.

## Verify after deploy
- Worker appears as registered/healthy against the executor (`REGISTRY_URL`).
- The `test_smtp_connection` design-time function succeeds against the target relay (proves
  egress + vault credential), then a real `email` node sends a test message.
