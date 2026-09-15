# Run the Workflow application locally (backend + frontend)

How to bring up the **AI Workflow** stack on a dev machine: the Python backend
services (`workflow/`) and the `@ldc/workflow-management` micro-frontend
(`apps/frontend/`). This assumes the **infrastructure (SAP HANA Express, Kafka,
Redis) is already running in Docker under WSL** — see
`core/docker-compose-setup/local-docker-compose-setup/`.

> Source of truth: this guide mirrors the working
> `.vscode/launch.json` run configs and `setup-wvenv.ps1` in this folder. If a
> command here drifts from those files, **the files win — fix this doc in the same change.**

---

## 0. Topology — who talks to whom

| Component | Where | Port | How to run |
|---|---|---|---|
| **HANA Express** (DB) | Docker/WSL | `39041` | `docker-compose.data-hana.yaml` (already up) |
| **Kafka** (PLAINTEXT) | Docker/WSL | `9092` | `docker-compose.kafka.yaml` (already up) |
| **Redis** (Socket.IO / cache) | Docker/WSL | `6379` | optional — falls back to in-memory if absent |
| **Workflow Control Plane** | this repo | `8001` | `workflow-control-plane-service/main.py` |
| **Worker Executor** | this repo | `8004` | `worker-executor-service/main.py` |
| **Frontend — workflow-management** | `apps/frontend` | `3004` | `bun run dev:workflow` (Module Federation remote) |
| **Frontend — shell host** | `apps/frontend` | `3000` | `bun run dev:shell` (federates the remote) |

The frontend reaches the backend via `apps/frontend/.env`:
`PUBLIC_URL_AI_WORKFLOW_CONTROL_PLANE=http://localhost:8001` (already set).

```
[ browser :3000 shell ] --federates--> [ :3004 workflow-management ]
                |  REST / Socket.IO
                v
        [ :8001 Control Plane ] --REST/Kafka--> [ :8004 Worker Executor ]
                |                                        |
                +------------- HANA :39041 / Kafka :9092 / Redis :6379 -----+
```

---

## 1. Prerequisites

- **Python 3.11+** on PATH (`py -3` or `python`).
- **Bun 1.3.11** for the frontend (`powershell -c "irm bun.sh/install.ps1 | iex"`).
- **Docker infra up in WSL.** Confirm the ports answer before starting the app:
  ```powershell
  Test-NetConnection localhost -Port 39041   # HANA
  Test-NetConnection localhost -Port 9092    # Kafka
  Test-NetConnection localhost -Port 6379    # Redis (optional)
  ```
  HANA local creds (from the compose `init.sql`): user `SYSTEM`, password
  `ATXSmall@123456`, schema `WF_WORKFLOW_CONTROLLER`.

---

## 2. Backend — one-time setup

From this folder (`apps/backend/ai-workflow-management/workflow/`):

```powershell
pwsh -File .\setup-wvenv.ps1
```

This creates `.wvenv\` and installs **every** `requirements*.txt` found under the
tree (control-plane, worker-executor, workers, SDKs). Re-run with `-Force` to
rebuild the venv from scratch. Activate it with:

```powershell
.\.wvenv\Scripts\Activate.ps1
```

---

## 3. Backend — run the services

### Option A (recommended): VS Code Run & Debug

Open this folder in VS Code and launch these configs from `.vscode/launch.json`
(they already encode every env var — HANA host, Kafka, Redis, Event Mesh creds, etc.):

1. **Workflow Control Plane (8001)** — the main service (REST + Socket.IO + gRPC).
2. **Worker Execution (8004)** — needed to actually *execute* workflows.

For a minimal "design / open the UI" loop you only need (1). To **run** a
workflow end-to-end, start (2) as well.

### Option B: terminal

Both services read the folder-level `.env` and run via `python main.py`. The venv
Python is `.wvenv\Scripts\python.exe`; `cwd` must be this folder and
`PYTHONPATH=.`.

```powershell
# --- Control Plane (terminal 1) ---
.\.wvenv\Scripts\Activate.ps1
$env:PYTHONPATH = "."; $env:SERVICE_PORT = "8001"; $env:INFRA_MODE = "local"
$env:HANA_HOST = "localhost"; $env:HANA_PORT = "39041"; $env:HANA_USERNAME = "SYSTEM"
$env:HANA_PASSWORD = "ATXSmall@123456"; $env:HANA_SCHEMA = "WF_WORKFLOW_CONTROLLER"; $env:HANA_ENCRYPT = "false"
$env:MESSAGING_MODE = "local"; $env:KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"; $env:SOCKETIO_REDIS_URL = "redis://localhost:6379"
python .\workflow-control-plane-service\main.py
```

```powershell
# --- Worker Executor (terminal 2) ---
.\.wvenv\Scripts\Activate.ps1
$env:PYTHONPATH = "."; $env:SERVICE_PORT = "8004"
$env:HANA_HOST = "localhost"; $env:HANA_PORT = "39041"; $env:HANA_USERNAME = "SYSTEM"
$env:HANA_PASSWORD = "ATXSmall@123456"; $env:HANA_SCHEMA = "WF_WORKFLOW_CONTROLLER"; $env:HANA_ENCRYPT = "false"
$env:KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
python .\worker-executor-service\main.py
```

> ⚠️ The terminal env above is the **minimal local set**. The Control Plane also
> uses extra vars (Event Mesh, `GATEWAY_REGISTRY_KEK_*`, `ENABLE_NODE_CATALOG_V2`,
> `USE_PROJECTOR_READ_MODEL`, `DISPATCHER_MODE`) — for the complete working set,
> copy them from the **"Workflow Control Plane (8001)"** block in `.vscode/launch.json`.
> If a feature behaves oddly from the terminal, that missing env is the usual cause;
> prefer Option A.

### Verify the backend

```powershell
curl http://localhost:8001/api/v1/health    # Control Plane
curl http://localhost:8004/health            # Worker Executor
```

---

## 4. Frontend — run the workflow UI

From `apps/frontend/`:

```bash
bun install                 # first time, or after dependency changes
bun run dev:workflow        # workflow-management remote on http://localhost:3004
```

- **Standalone:** open `http://localhost:3004` to work on the workflow UI in isolation.
- **Full app (recommended):** also run the host so the remote loads inside the real shell:
  ```bash
  bun run dev:shell         # shell host on http://localhost:3000
  ```
  Then open `http://localhost:3000`.

The frontend's API targets live in `apps/frontend/.env`. The only one pointed at
your local backend is the control plane
(`PUBLIC_URL_AI_WORKFLOW_CONTROL_PLANE=http://localhost:8001`); the rest
(file service, governance, agents, push gateway) point at the **shared dev (cloud)**
environment, which is fine for local work.

---

## 5. Gotchas

- **HANA host conflict in `.env` files.** `workflow-control-plane-service/.env`
  points `HANA_HOST` at a **cloud** HANA, while the folder-level `.env` and the
  VS Code launch env point at **localhost:39041**. The launch config / Option-B
  env override wins at runtime — make sure you're running with `HANA_HOST=localhost`
  for the local stack, not the per-service `.env`.
- **Redis is optional.** Without it, Socket.IO and caching fall back to in-memory
  (single-instance) — fine for one dev machine. Realtime push still works.
- **Worker Executor uses `MESSAGING_MODE=sap`** in the launch config (Event Mesh),
  while the Control Plane uses `local`. Keep that distinction; don't "normalize" them.
- **Two entry points exist.** The Control Plane is `main.py` (HTTP). There is also
  `app/executor_main.py` (standalone executor, no HTTP) used in some deploys —
  not needed for the local UI loop.
- **`@ldc/*` frontend packages** resolve from the Bun workspace catalog; if
  `bun install` errors, try `bun install --force`.

> 🔐 **Security note:** `.vscode/launch.json` and the `.env` files in this folder
> currently contain **real, live credentials** (an OpenAI API key, Event Mesh
> client secret, HANA passwords). These should be rotated and moved out of version
> control (use a gitignored `.env.local` / personal launch config). Flagging, not
> fixing, here — out of scope for this run guide.

---

## 6. TL;DR

```powershell
# infra already up in WSL (HANA 39041 / Kafka 9092 / Redis 6379)

# backend (once)
pwsh -File .\setup-wvenv.ps1
# backend (each run) — easiest: VS Code → Run "Workflow Control Plane (8001)" + "Worker Execution (8004)"

# frontend
cd ..\..\..\..\frontend   # apps/frontend
bun install
bun run dev:workflow      # :3004   (+ `bun run dev:shell` for :3000 host)
```
