# Task & Workers

The `TASK` node is how a workflow does work outside the engine — HTTP calls, AI inference, SQL, notifications, and anything else a worker provides.

## TASK

**Executes a task via a registered worker.** Runtime: **worker** (completion mode `dispatched`). The control plane resolves the node's input, hands it to the [Worker Executor](/guide/architecture#worker-executor), and waits for the worker's result.

- **Input ports:** `in_default`
- **Output ports:** `success`, `failure`

### How it works

1. You set a `worker_type` and the worker's input fields in the node config.
2. At runtime the engine resolves all `{{...}}` expressions in the config.
3. The executor routes the task to whichever worker registered for that `worker_type`.
4. The worker runs and returns a structured result, which becomes the node's output (and fires `success` or `failure`).

### Config

The `TASK` node's own declared field is minimal — the **rest of the config is the selected worker's input schema**:

| Field | Type | Expr | Notes |
|---|---|---|---|
| `input_types` | object | | Type hints for the resolved input. *(internal)* |
| `worker_type` | string | | Which worker to dispatch to, e.g. `http_request`, `agent`. |
| *(worker fields)* | varies | varies | The selected worker's input fields — see the [Workers reference](/reference/workers). |

### Output

`TASK` declares no fixed output fields; **the worker determines the output shape.** For example an `http_request` task returns `status_code`, `response_body`, `elapsed_ms`, etc. Look up the chosen worker's output schema in the [Workers reference](/reference/workers).

### Example — HTTP request

```json
{
  "id": "fetch-user",
  "type": "TASK",
  "config": {
    "worker_type": "http_request",
    "method": "GET",
    "url": "https://api.example.com/users/{{$input.user_id}}",
    "auth_type": "bearer",
    "auth_token": "{{$variables.api_token}}"
  }
}
```

Downstream: `{{$fetch-user.output.response_body}}`, `{{$fetch-user.output.status_code}}`. Route `failure` to an error handler.

### Example — AI agent

```json
{
  "id": "summarize",
  "type": "TASK",
  "config": {
    "worker_type": "agent",
    "provider": "openai",
    "model": "gpt-4o",
    "api_key": "{{$variables.openai_key}}",
    "user_prompt": "{{$summary-prompt.output.content}}"
  }
}
```

## Available workers

| Worker | `worker_type` | What it does |
|---|---|---|
| HTTP Request | `http_request` | HTTP/REST calls with auth, headers, query params, and body. |
| Agent | `agent` | LLM inference (OpenAI) — prompts, history, temperature, token limits. |
| Mapping Data | `mapping_data` | Field- and array-level data transformation. |
| Wait | `wait` | Fixed-duration, until-datetime, or manual-approval waits. |
| Database Connection | `database_connection` | SQL (SELECT/INSERT/UPDATE/DELETE) on PostgreSQL, MySQL, HANA. |
| MS Teams | `ms_teams` | Send messages and adaptive cards to Teams. |
| Gateway | `gateway-worker` | Passthrough executor for resolved gateway-function calls. |

Each worker's **full input/output schema** is documented in the [Workers reference](/reference/workers). To use one, run it (see [Workers setup](/guide/workers-setup)) and set the `TASK` node's `worker_type` to its identifier.

::: warning A TASK needs its worker running
If no worker is registered for the `worker_type`, the task cannot be dispatched. Make sure the worker is started **and** registered with the executor (`REGISTRY_URL=http://localhost:8004`) before running a workflow that uses it.
:::
