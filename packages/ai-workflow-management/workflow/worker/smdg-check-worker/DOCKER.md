# SMDG Check Worker — Docker Build & Run

A local **PULL** worker for the ai-workflow system. Node types:
`qc_duplication_check`, `qc_scope_auth`, `qc_onprem_data`. Pure HTTP/OAuth — the image
needs **no** Node.js, git or Claude CLI.

---

## Prerequisites

- **Docker Engine** + **Docker Compose v2** (`docker compose …`, *not* `docker-compose`).
- No credential file needed — the node OAuth credential comes from the flow's
  environment/Section (`{{$env.OAUTH}}`), not from this worker.

> The build context is the **parent `worker/` directory**, because this worker installs the
> sibling package `worker-sdk/` (`pip install -e ./worker-sdk`). The compose file sets that for you.

---

## 1. Config (`.env` — run config only, no secrets)

Read at **runtime** from `smdg-check-worker/.env`, injected as env vars by Compose:

```dotenv
APP_MODE=PULL
REGISTRY_URL=https://smdg-ai-tenant-1-ai-workflow-worker-executor.cfapps.br10.hana.ondemand.com
WORKER_TENANT=
WORKER_MAX_CONCURRENT=1
```

Credentials and endpoint URLs are **not** here — they come from the flow environment
(`{{$env.OAUTH}}`, `{{$env.*_BASE_API_URL}}`).

---

## 2. Build + run

From this `smdg-check-worker/` directory:

```bash
docker compose up -d --build     # build + start, detached
docker compose logs -f           # follow logs
docker compose ps                # status
docker compose down              # stop + remove
```

A healthy start prints:

```
Pull worker '<type>' registered as <uuid>
... worker running (multi-type, leasing 3 node types: qc_duplication_check, qc_scope_auth, qc_onprem_data). Ctrl+C to stop.
```

---

## 3. Deploy to a Linux server

Portable — no host mounts, no secrets on the worker.

```bash
# 1) Copy the worker/ tree to the server (must include worker-sdk/ and smdg-check-worker/).
# 2) Create smdg-check-worker/.env (section 1).
# 3) Build + run:
cd .../workflow/worker/smdg-check-worker
docker compose up -d --build
docker compose logs -f
```

`restart: unless-stopped` keeps it running across crashes/reboots
(`sudo systemctl enable --now docker`).

---

## 4. Troubleshooting

| Symptom | Fix |
|---|---|
| Node fails with `No credential` | The flow's Section must define the `$env` credential the node's `credential_ref` points at (e.g. `OAUTH`). |
| `Temporary failure in name resolution` (lease calls drop) | The compose pins `8.8.8.8` / `1.1.1.1`; ensure the host can reach them. |
| Rebuild after code changes | `docker compose build --no-cache` then `up -d`. |

---

## Image

`python:3.12-slim` + `worker-sdk` (editable). Small — the nodes use only the Python stdlib.
No secrets are baked in — `worker/.dockerignore` excludes `**/.env` and `**/*secrets*.json`.
