# Running a Workflow

This walkthrough takes you from an empty system to a completed run, using the REST API. Every endpoint here is documented in full on the [REST API](/reference/rest-api) page; this page is the happy path.

All endpoints are under the control plane base URL: **`http://localhost:8001/api/v1`**.

## Step 1 — Create a workflow

A workflow is a graph of nodes and edges. The smallest meaningful workflow is a `TRIGGER` → `TASK` → `OUTPUT`.

```bash
curl -X POST http://localhost:8001/api/v1/workflows \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Fetch and report",
    "nodes": [
      { "id": "trigger-1", "type": "TRIGGER", "config": { "trigger_type": "manual" } },
      { "id": "http-1", "type": "TASK", "config": {
          "worker_type": "http_request",
          "method": "GET",
          "url": "https://api.github.com/repos/cli/cli"
      }},
      { "id": "out-1", "type": "OUTPUT", "config": {
          "output_schema": [
            { "key": "stars", "type": "number", "value": "{{$http-1.output.response_body.stargazers_count}}" }
          ]
      }}
    ],
    "edges": [
      { "source": "trigger-1", "sourcePort": "out_default", "target": "http-1", "targetPort": "in_default" },
      { "source": "http-1", "sourcePort": "success", "target": "out-1", "targetPort": "in_default" }
    ]
  }'
```

The response contains the new `workflow_id` (e.g. `wf-1a2b3c`). The exact node/edge shape your UI sends may differ slightly — what matters is each node's `type` + `config` and how ports are wired.

::: tip Validate before you run
`POST /workflows/{workflow_id}/dry-run` runs structural (DAG) validation without executing anything. Use it to catch disconnected nodes, bad ports, and cycles early.
:::

## Step 2 — Start a run

```bash
curl -X POST http://localhost:8001/api/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{
    "workflow_id": "wf-1a2b3c",
    "input_data": { "repo": "cli/cli" }
  }'
```

The response includes the `run_id`. The `input_data` you pass is available downstream as `{{$input.*}}`.

Useful optional fields on the start-run request:

| Field | Default | Purpose |
|---|---|---|
| `input_data` | `{}` | The run's input payload, readable as `{{$input.*}}`. |
| `tenant_id` | `system` | Tenancy scope. |
| `correlation_id` | `""` | Correlate this run with an external event. |
| `skip_validation` | `true` | Skip DAG validation at start (set `false` to enforce). |
| `user_variables` | `{}` | Seed run-scoped `{{$variables.*}}`. |
| `metadata` | `{}` | Arbitrary metadata stored on the run. |

## Step 3 — Watch the run

Poll its status, or subscribe to Socket.IO (room `run:{run_id}`) for live events.

```bash
# overall run status + state
curl http://localhost:8001/api/v1/runs/{run_id}

# per-node status (RUNNING / COMPLETED / PENDING / FAILED …)
curl http://localhost:8001/api/v1/runs/{run_id}/nodes/status

# the detailed execution log
curl http://localhost:8001/api/v1/runs/{run_id}/execution-log

# final outputs / artifacts
curl http://localhost:8001/api/v1/runs/{run_id}/artifacts
```

A run ends in `COMPLETED`, `FAILED`, or `CANCELLED`. The [`OUTPUT`](/nodes/entry-output#output) node's fields become the run's `output_data`.

## Step 4 — If the run pauses for a human

If your workflow contains a [human-in-the-loop](/nodes/human-in-the-loop) node, the run enters a **pending** state at that node and waits. The node status will be `PENDING`. How you resume depends on the node:

### INPUT — submit data

```bash
curl -X POST http://localhost:8001/api/v1/runs/{run_id}/nodes/{node_id}/submit \
  -H 'Content-Type: application/json' \
  -d '{ "node_id": "input-1", "output_data": { "approved_amount": 500 } }'
```

### HUMAN_ACTION — approve or reject

Submit through the same `/submit` endpoint; the payload carries the chosen action (`confirm` / `reject`) and any collected fields. The node then fires its `confirm` or `reject` port.

### DATA_EDIT — edit data in place

Use `PATCH /runs/{run_id}/nodes/{node_id}/data` to push edited rows. Pass the current `if_match` etag to guard against a stale write.

### FILE_UPLOAD — provide a file

The run waits until a file is uploaded and registered, then resumes with the file's id/name/size available as the node's output.

See [Human-in-the-Loop nodes](/nodes/human-in-the-loop) for the exact submit payloads.

## Step 5 — Control the run (optional)

| Action | Endpoint |
|---|---|
| Pause | `POST /runs/{run_id}/pause` |
| Resume | `POST /runs/{run_id}/resume` |
| Cancel | `POST /runs/{run_id}/cancel` |
| Rerun whole run | `POST /runs/{run_id}/rerun` |
| Replay pending requests | `POST /runs/{run_id}/replay-pending-requests` |

Details and semantics: [Run Lifecycle & Control](./lifecycle).

## Testing without a full run

While designing, you can exercise pieces in isolation:

- **Test a single node** — `POST /workflows/{workflow_id}/nodes/{node_id}/test` runs one node with sample inputs.
- **Full test run** — `POST /workflows/{workflow_id}/test/full` executes the whole graph against a stable test run id.
- **Reset test state** — `POST /workflows/{workflow_id}/test/reset` clears the test run so you can re-run cleanly.
- **Variable autocomplete** — `GET /workflows/{workflow_id}/nodes/{node_id}/variable-suggestions?q=` returns `{{$...}}` suggestions while editing a field.

Next: learn the [expression syntax](./expressions) you used in the `OUTPUT` node above.
