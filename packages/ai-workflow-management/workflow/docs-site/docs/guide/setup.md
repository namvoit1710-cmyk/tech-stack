# Installation & Run

This page gets the **control plane** and **worker executor** running locally. The default `mock` mode needs no database, no broker, and no cloud — it is the fastest way to try the engine.

## Prerequisites

- **Python** (the version pinned in each service's `pyproject.toml` / `requirements.txt`).
- A virtual environment per service is recommended.

## 1. Start the Control Plane

```bash
cd workflow/workflow-control-plane-service

# install dependencies (first time)
pip install -r requirements.txt

# copy the example env and adjust if needed
cp .env.example .env

# run it
python main.py
```

The control plane listens on **`http://localhost:8001`** by default.

::: tip Zero-dependency mode
With `INFRA_MODE=mock` (the default in `.env.example`) the control plane uses in-memory repositories and console messaging. You do not need HANA, Kafka, or Redis to start designing and running workflows.
:::

### Key environment variables

| Variable | Default | Purpose |
|---|---|---|
| `INFRA_MODE` | `mock` | Backend selection: `mock` / `sqlite` / `local` / `sap`. See [Architecture](./architecture#infrastructure-modes-infra-mode). |
| `APP_MODE` | `REST` | Process entrypoint. Only `REST` is supported today. |
| `SERVICE_PORT` | `8001` | HTTP listener port. |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. |
| `TRANSPORT_MODE` | `http` | `http`, or `grpc` to also open a gRPC listener on `GRPC_PORT`. |
| `WORKER_EXECUTION_URL` | `http://localhost:8004` | Where the worker executor lives. Needed for any `TASK` node. |
| `CORS_ORIGINS` | `*` | Allowed origins for the REST API. |
| `REDIS_URL` | *(unset)* | Enables multi-instance Socket.IO fan-out. Falls back to in-memory. |
| `ENABLE_NODE_CATALOG_V2` | `false` | Exposes the v2 node catalog (gateway functions / custom nodes). |

Optional dependent services (leave empty to disable): `AGENT_SERVICE_URL`, `FILE_SERVICE_URL`, `DATA_FACTORY_URL`, `SCHEMA_TRANSFORM_URL`, `PUSH_GATEWAY_URL`.

When `INFRA_MODE=sap` you also provide `HANA_*` and `EVENT_MESH_*` credentials (or a `VCAP_SERVICES` JSON, which takes priority). When `INFRA_MODE=local` you provide `KAFKA_BOOTSTRAP_SERVERS` (default `localhost:9092`).

## 2. Start the Worker Executor

Required as soon as you use a `TASK` node. (Pure engine + human-in-the-loop workflows run without it.)

```bash
cd workflow/worker-executor-service

pip install -r requirements.txt
python main.py
```

It listens on **`http://localhost:8004`** by default.

| Variable | Default | Purpose |
|---|---|---|
| `SERVICE_PORT` | `8004` | HTTP listener port. |
| `INFRA_MODE` | `mock` | Same options as the control plane. |
| `WORKFLOW_CONTROL_PLANE_URL` | `http://localhost:8001` | Where to report task results back. |

## 3. Start at least one worker

Workers register themselves with the executor. See the dedicated [Workers setup](./workers-setup) page — the most important detail is that **every worker must export `REGISTRY_URL` pointing at the executor** before it starts.

```bash
cd workflow/worker/http-request-worker
export REGISTRY_URL=http://localhost:8004    # ← critical, default :8000 is wrong
python main.py
```

## 4. Verify it is up

```bash
# list workflows (empty array on a fresh mock instance)
curl http://localhost:8001/api/v1/workflows

# start designing — see "Running a Workflow"
```

## Recommended startup order

1. **Control Plane** (`:8001`)
2. **Worker Executor** (`:8004`)
3. **Workers** (`:35000`+) — each with `REGISTRY_URL=http://localhost:8004`

The control plane and executor are independent; workers depend on the executor being reachable at `REGISTRY_URL` when they register.

## Ports at a glance

| Service | Port |
|---|---|
| Control Plane (HTTP) | `8001` |
| Control Plane (gRPC, optional) | `50051` |
| Worker Executor (HTTP) | `8004` |
| Worker Executor (gRPC, optional) | `50052` |
| http-request-worker | `35000` |
| wait-worker | `35000` |
| gateway-worker | `35001` |
| database-connection-worker | `37000` |
| ms-teams-worker | `38000` |
| Kafka (`local` mode) | `9092` |
| Redis (optional) | `6379` |

Next: [run a worker](./workers-setup), then [run your first workflow](./running-a-workflow).
