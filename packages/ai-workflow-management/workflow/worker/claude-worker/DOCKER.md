# Claude Worker — Docker Build & Run

A local **PULL** worker for the ai-workflow system. Node types:
`extract_reqid`, `summarize_log`, `claude_check`, `claude_implement`, `claude_code`.
The worker shells out to the **Claude Code CLI** (`claude -p`) and to `git`, so the image
bundles Node.js + `@anthropic-ai/claude-code` + git.

---

## Prerequisites

- **Docker Engine** + **Docker Compose v2** (`docker compose …`, *not* `docker-compose`).
- A `claude-worker/.env` credential file (git-ignored — never committed, never baked into the image).

> The build context is the **parent `worker/` directory**, because this worker installs the
> sibling package `worker-sdk/` (`pip install -e ./worker-sdk`). The compose file sets that for you.

---

## 1. Credentials (`.env` — env-first)

Read at **runtime** from `claude-worker/.env` (see `app/secrets.py`), injected as env vars by Compose:

```dotenv
CLAUDE_WORKER_GITHUB_PAT=...
CLAUDE_WORKER_GITLAB_PAT=...
# the Claude Code CLI authenticates with this OAuth token (comma-separate for rotation)
CLAUDE_WORKER_CLAUDE_CLI_TOKEN=...
```

---

## 2. Build + run

From this `claude-worker/` directory:

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

The Claude CLI is bundled — verify with:

```bash
docker exec claude-worker claude --version
```

> To build/run **both** workers (log + claude) at once, use `worker/docker-compose.yml`:
> `cd .. && docker compose up -d`.

---

## 3. Deploy to a Linux server

Portable — no host mounts; credentials come from `.env` only.

```bash
# 1) Copy the worker/ tree to the server (must include worker-sdk/ and claude-worker/).
# 2) Create claude-worker/.env (section 1).
# 3) Build + run:
cd .../workflow/worker/claude-worker
docker compose up -d --build
docker compose logs -f
```

`restart: unless-stopped` keeps it running across crashes/reboots
(`sudo systemctl enable --now docker`).

---

## 4. Troubleshooting

| Symptom | Fix |
|---|---|
| Claude calls fail auth | Set `CLAUDE_WORKER_CLAUDE_CLI_TOKEN` in `.env`. |
| `--dangerously-skip-permissions cannot be used with root` | The image sets `IS_SANDBOX=1`; don't override it (the container IS the sandbox). |
| `Temporary failure in name resolution` (lease calls drop) | The compose pins `8.8.8.8` / `1.1.1.1`; ensure the host can reach them. |
| Rebuild after code changes | `docker compose build --no-cache` then `up -d`. |

---

## Image

`python:3.12-slim` + `git` + Node 20 + `@anthropic-ai/claude-code` + `worker-sdk` (editable).
Runs the CLI as root inside the container (`IS_SANDBOX=1`). Credentials are **never** baked in —
`worker/.dockerignore` excludes `**/.env` and `**/*secrets*.json`.
