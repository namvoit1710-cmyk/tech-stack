# AI Workflow Management — Getting Started

Guide to running the full platform locally from VSCode using a **single
root-level `.venv`** and the pre-configured `launch.json`.

## 1. Open the workspace in VSCode

Open VSCode at the **monorepo root** (not the `workflow/` sub-folder):

```
apps/backend/ai-workflow-management
```

Absolute path on this machine:

```
c:\Users\EdgarNguyen\workspace\project\github\simplemdg_ai-new-main\apps\backend\ai-workflow-management
```

This is the `${workspaceFolder}` every launch config and setting points at.
Opening a deeper folder will break relative paths in
[.vscode/launch.json](.vscode/launch.json) and
[.vscode/settings.json](.vscode/settings.json).

### Workspace layout

| Path | Purpose |
|------|---------|
| [.vscode/launch.json](.vscode/launch.json) | Debug configs for every service |
| [.vscode/settings.json](.vscode/settings.json) | Python interpreter, formatter, analysis paths |
| [.env](.env) | Shared env file loaded by every launch config |
| [.venv/](.venv/) | **Single virtualenv for the whole workspace** — you create this in step 3 |
| [api-gateway-service/](api-gateway-service/) | API gateway — port 8000 |
| [workflow/workflow-control-plane-service/](workflow/workflow-control-plane-service/) | Orchestration engine — port 8001 |
| [workflow/worker-executor-service/](workflow/worker-executor-service/) | Worker registry & dispatcher — port 8004 |
| [workflow/worker/worker-sdk/](workflow/worker/worker-sdk/) | Shared SDK for all workers |
| [workflow/worker/http-request-worker/](workflow/worker/http-request-worker/) | HTTP-request worker — port 35000/36000 |
| [workflow/worker/wait-worker/](workflow/worker/wait-worker/) | Wait worker |
| [workflow/worker/agent-worker/](workflow/worker/agent-worker/) | Agent worker |
| [workflow/worker/mapping-data-worker/](workflow/worker/mapping-data-worker/) | Mapping-data worker |
| [workflow/docker-compose.yml](workflow/docker-compose.yml) | Optional local infra (Kafka, Redis, HANA) |

## 2. Prerequisites

- **Python 3.12**
- **VSCode** with the **Python** and **Python Debugger (debugpy)** extensions
- **Git**
- **Redis** on `localhost:6379` (Socket.IO + cache)
- **Kafka** on `localhost:9092` (messaging — `MESSAGING_MODE=local`)
- **SAP HANA** on `localhost:39041` (persistence — `INFRA_MODE=local` with
  HANA, password in launch.json is `ATXSmall@123456`)

Spin up Redis + Kafka + HANA with:

```bash
cd workflow
docker compose up -d zookeeper kafka redis hana
```

> To skip HANA/Kafka, switch `INFRA_MODE` to `mock` and `MESSAGING_MODE` to
> `console` in `launch.json` — everything will run in-memory.

## 3. Create the root `.venv` and install everything

All launch configs point at `${workspaceFolder}/.venv/Scripts/python.exe`,
so you need exactly **one** venv at the workspace root.

From the workspace root (`apps/backend/ai-workflow-management`):

```bash
python -m venv .venv
.venv\Scripts\activate                 # Windows (PowerShell: .venv\Scripts\Activate.ps1)
# source .venv/bin/activate            # macOS/Linux

python -m pip install --upgrade pip

# Install every service into the same venv
pip install -r api-gateway-service/requirements.txt
pip install -r workflow/workflow-control-plane-service/requirements.txt
pip install -r workflow/worker-executor-service/requirements.txt

# Worker SDK first (other workers depend on it via -e ../worker-sdk)
pip install -e workflow/worker/worker-sdk

pip install -r workflow/worker/http-request-worker/requirements.txt
pip install -r workflow/worker/wait-worker/requirements.txt
pip install -r workflow/worker/agent-worker/requirements.txt
pip install -r workflow/worker/mapping-data-worker/requirements.txt
```

VSCode should auto-detect `.venv`. If it doesn't, press
`Ctrl+Shift+P → Python: Select Interpreter` and pick
`.venv\Scripts\python.exe`. The interpreter is pinned by
[.vscode/settings.json](.vscode/settings.json).

## 4. Create the root `.env`

Each launch config sets `"envFile": "${workspaceFolder}/.env"`. Create
`.env` at the workspace root with anything you want shared across services
(secrets, HANA creds, external service URLs). Per-service overrides already
live in the `env` block of each launch config, so `.env` can start empty:

```dotenv
# .env — shared across all launch configs
# Override or add values here; launch.json env wins for duplicates.
```

## 5. Start services from VSCode (Run and Debug panel)

Open **Run and Debug** (`Ctrl+Shift+D`) and pick a config from
[.vscode/launch.json](.vscode/launch.json). Start them in this order:

1. **API Gateway (8000)** — entry point for clients
2. **Workflow Control Plane (8001)** — orchestration engine
3. **Worker Execution (8004)** — worker registry & dispatcher
4. **Worker ${input:worker_name} (${input:worker_port})** — prompts for
   worker name + port, launches any worker under `workflow/worker/`

The worker config prompts twice:

| Input | Default | Example values |
|-------|---------|----------------|
| `worker_name` | `http-request-worker` | `wait-worker`, `agent-worker`, `mapping-data-worker` |
| `worker_port` | `36000` | pick a unique port per worker |

Each config opens its own integrated terminal; run several in parallel
(one worker per port). To run **all** at once, pick **Run → Start Debugging**
per config, or use a compound — see section 6.

## 6. Optional: one-click "start everything" compound

Add this to the `configurations` list' sibling in `launch.json` to start
API Gateway + Control Plane + Executor together:

```json
"compounds": [
  {
    "name": "All Services",
    "configurations": [
      "API Gateway (8000)",
      "Workflow Control Plane (8001)",
      "Worker Execution (8004)"
    ],
    "stopAll": true
  }
]
```

Workers are excluded because they need per-instance `worker_name` /
`worker_port` inputs — launch them individually.

## 7. Verify

- Gateway:       [http://localhost:8000/docs](http://localhost:8000/docs)
- Control plane: [http://localhost:8001/docs](http://localhost:8001/docs)
- Executor:      [http://localhost:8004/docs](http://localhost:8004/docs)
- Worker health: `curl http://localhost:36000/health`
- Registered workers: `curl http://localhost:8004/api/v1/workers`

## 8. Tests

With `.venv` activated, from the workspace root:

```bash
python -m pytest workflow/workflow-control-plane-service/tests -v
python -m pytest workflow/worker-executor-service/tests -v
python -m pytest workflow/worker/worker-sdk/tests -v
python -m pytest workflow/worker/http-request-worker/tests -v
```

## 9. Troubleshooting

| Symptom | Fix |
|---------|-----|
| Launch says `python.exe` not found | Create `.venv` at the workspace root (step 3) |
| `ModuleNotFoundError: worker_sdk` | `pip install -e workflow/worker/worker-sdk` |
| HANA / Kafka connection errors | `docker compose up -d` from `workflow/`, or switch to `INFRA_MODE=mock` |
| Worker can't register | Confirm `REGISTRY_URL=http://localhost:8004` (set by the worker launch config) |
| Port already in use | Change the port in `launch.json` env or the worker port input |
| Breakpoints don't hit | Make sure the Python interpreter in the status bar is `.venv\Scripts\python.exe` |

See [CLAUDE.md](workflow/CLAUDE.md) for architecture and
[workflow/worker/worker-sdk/README.md](workflow/worker/worker-sdk/README.md)
for writing new workers.
