# SMDG Log Worker — Docker Build & Run

A local **PULL** worker for the ai-workflow system. Node types:
`request_action_log`, `submit_event_log`, `request_log`, `cloud_logging_search`,
`cf_requests_logs_search`. Pure HTTP/OAuth — the image needs **no** Node.js, git or Claude CLI.

---

## Prerequisites

- **Docker Engine** + **Docker Compose v2** (`docker compose …`, *not* `docker-compose`).
- A `log-worker/.env` credential file (git-ignored — never committed, never baked into the image).

> The build context is the **parent `worker/` directory**, because this worker installs the
> sibling package `worker-sdk/` (`pip install -e ./worker-sdk`). The compose file sets that for you.

---

## 1. Credentials (`.env` — env-first)

Read at **runtime** from `log-worker/.env` (see `app/secrets.py`), injected as env vars by Compose:

```dotenv
LOG_WORKER_CLIENT_ID=...
LOG_WORKER_CLIENT_SECRET=...
# QAS OAuth client — used automatically when the target host contains "-qas"
LOG_WORKER_CLIENT_ID_QAS=...
LOG_WORKER_CLIENT_SECRET_QAS=...
# BTP Cloud Logging (OpenSearch) — only if you use the cloud_logging_search node
CLS_URL=...
CLS_USERNAME=...
CLS_PASSWORD=...
```

---

## 2. Build + run

From this `log-worker/` directory:

```bash
docker compose up -d --build     # build + start, detached
docker compose logs -f           # follow logs
docker compose ps                # status
docker compose down              # stop + remove
```

A healthy start prints:

```
registered node type '<name>' as <uuid>
... worker running (multi-type, leasing 5 node types). Ctrl+C to stop.
```

> To build/run **both** workers (log + claude) at once, use `worker/docker-compose.yml`:
> `cd .. && docker compose up -d`.

---

## 3. Deploy to a Linux server

Portable — no host mounts; credentials come from `.env` only.

```bash
# 1) Copy the worker/ tree to the server (must include worker-sdk/ and log-worker/).
# 2) Create log-worker/.env (section 1).
# 3) Build + run:
cd .../workflow/worker/log-worker
docker compose up -d --build
docker compose logs -f
```

`restart: unless-stopped` keeps it running across crashes/reboots
(`sudo systemctl enable --now docker`).

---

## 4. Troubleshooting

| Symptom | Fix |
|---|---|
| `client_id/client_secret not found` | Set `LOG_WORKER_CLIENT_ID` / `_SECRET` in `.env` (or the `_QAS` pair for a `-qas` host). |
| `Temporary failure in name resolution` (lease calls drop) | The compose pins `8.8.8.8` / `1.1.1.1`; ensure the host can reach them. |
| Rebuild after code changes | `docker compose build --no-cache` then `up -d`. |

---

## Image

`python:3.12-slim` + `worker-sdk` (editable). Small — the nodes use only the Python stdlib.
Credentials are **never** baked in — `worker/.dockerignore` excludes `**/.env` and `**/*secrets*.json`.
