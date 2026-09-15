# Workers Setup

Workers are the processes that execute `TASK` nodes. The control plane never makes business HTTP calls, runs AI inference, or executes SQL itself — it dispatches that work to a worker through the [Worker Executor](./architecture#worker-executor).

## How registration works

Each worker is built on the **worker SDK**. At startup it calls `run_worker(...)`, which:

1. Reads the worker's `WORKER_TYPE` and input/output schema.
2. Registers the worker with the executor at **`REGISTRY_URL`**.
3. Starts an HTTP listener (in `SERVER` mode) or a long-poll loop (in `HEADLESS` mode) to receive tasks.

When the control plane reaches a `TASK` node with `worker_type: "http_request"`, the executor routes it to whichever worker registered under that type.

## The one rule you must not forget

::: danger Always set `REGISTRY_URL`
The SDK defaults `REGISTRY_URL` to `http://localhost:8000`, which is **not** the executor's port. If you forget to override it, the worker starts but **silently fails to register** and your `TASK` nodes will never run.

```bash
export REGISTRY_URL=http://localhost:8004   # the Worker Executor
```
:::

## Starting a worker

```bash
cd workflow/worker/http-request-worker

pip install -r requirements.txt

export REGISTRY_URL=http://localhost:8004    # executor endpoint
export WORKER_TYPE=http_request              # usually already set in config.py
export SERVER_PORT=35000                      # this worker's own port

python main.py
```

On a successful start you will see a line like:

```
Mode: SERVER on localhost:35000
```

…and the worker appears in the executor's registry.

## Worker environment variables

| Variable | Default | Purpose |
|---|---|---|
| `REGISTRY_URL` | `http://localhost:8000` ⚠️ | **Override to `http://localhost:8004`** — the executor. |
| `WORKER_TYPE` | per worker | The identifier this worker registers under (`http_request`, `agent`, …). |
| `SERVER_PORT` | per worker | The worker's own HTTP port. |
| `SERVER_HOST` | `localhost` | Bind address in `SERVER` mode. |
| `APP_MODE` | `SERVER` | `SERVER` (HTTP listener) or `HEADLESS` (long-poll, no HTTP server). |

::: warning CRLF in `.env` files (WSL / Windows)
Worker type matching is exact. A trailing carriage return (`\r`) from a CRLF-encoded `.env` can make `WORKER_TYPE` fail to match. If a worker registers but tasks never route to it, set the env vars explicitly in the shell instead of relying on a `.env`.
:::

## The built-in workers

| Worker | `worker_type` | Default port | Does |
|---|---|---|---|
| http-request-worker | `http_request` | `35000` | HTTP/REST calls with auth. |
| agent-worker | `agent` | *(SDK default)* | LLM inference (OpenAI). |
| mapping-data-worker | `mapping_data` | *(SDK default)* | Field/array data mapping. |
| wait-worker | `wait` | `35000` | Timed or manual waits. |
| database-connection-worker | `database_connection` | `37000` | SQL on PostgreSQL / MySQL / HANA. |
| ms-teams-worker | `ms_teams` | `38000` | MS Teams messages & adaptive cards. |
| gateway-worker | `gateway-worker` | `35001` | Gateway-function passthrough. |

Full input/output schemas for each are in the [Workers reference](/reference/workers). Run only the workers your workflows actually use — each is an independent process.

## Adding your own worker

Build a new service with the worker SDK, give it a unique `WORKER_TYPE`, declare its input/output schema, implement the task handler, and start it with `REGISTRY_URL` pointing at the executor. Once registered, drop a `TASK` node on the canvas and set its `worker_type` to your new identifier.
