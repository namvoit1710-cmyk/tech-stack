# REST API Reference

All endpoints are served by the **control plane** under the base path **`/api/v1`** (default host `http://localhost:8001`). The v2 node-catalog endpoint is under `/api/v2`.

## Workflows

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/workflows` | Create a workflow. |
| `GET` | `/workflows` | List workflows (paginated). |
| `GET` | `/workflows/main` | Get the primary/main workflow. |
| `GET` | `/workflows/by-path/{routing_path}` | Look up a workflow by routing path. |
| `GET` | `/workflows/{workflow_id}` | Get a workflow. |
| `GET` | `/workflows/{workflow_id}/version/{version}` | Get a specific version. |
| `GET` | `/workflows/{workflow_id}/node-tree` | Get the workflow's node tree. |
| `PUT` | `/workflows/{workflow_id}` | Replace a workflow. |
| `PATCH` | `/workflows/{workflow_id}` | Partially update a workflow. |
| `PATCH` | `/workflows/{workflow_id}/main-flow` | Update the main flow. |
| `DELETE` | `/workflows/{workflow_id}` | Delete a workflow. |
| `GET` | `/workflows/publish/{workflow_id}` | Check publish status. |
| `POST` | `/workflows/publish/{workflow_id}` | Publish a workflow version. |

### Design-time testing

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/workflows/{workflow_id}/dry-run` | Structural (DAG) validation, no execution. |
| `POST` | `/workflows/{workflow_id}/test/full` | Full test run against a stable test run id. |
| `POST` | `/workflows/{workflow_id}/test/reset` | Reset the test run state. |
| `POST` | `/workflows/{workflow_id}/nodes/{node_id}/test` | Test a single node with sample input. |
| `GET` | `/workflows/{workflow_id}/nodes/{node_id}/fields` | Get a node's field definitions. |
| `GET` | `/workflows/{workflow_id}/nodes/{node_id}/variable-suggestions?q=` | `{{$...}}` autocomplete suggestions. |

## Runs

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/runs` | Start a run. |
| `GET` | `/runs` | List runs (paginated). |
| `GET` | `/runs/{run_id}` | Get run status + state. |
| `GET` | `/runs/{run_id}/nodes/status` | Per-node statuses. |
| `GET` | `/runs/{run_id}/children` | Child (sub-workflow) runs. |
| `GET` | `/runs/{run_id}/execution-log` | Detailed execution log. |
| `GET` | `/runs/{run_id}/context` | Variable scope / context. |
| `GET` | `/runs/{run_id}/artifacts` | Output files / artifacts. |
| `POST` | `/runs/{run_id}/pause` | Pause the run. |
| `POST` | `/runs/{run_id}/resume` | Resume the run. |
| `POST` | `/runs/{run_id}/cancel` | Cancel the run. |
| `POST` | `/runs/{run_id}/rerun` | Re-execute the run. |
| `POST` | `/runs/{run_id}/replay-pending-requests` | Re-emit pending input requests. |
| `POST` | `/runs/{run_id}/resolve-variable-paths` | Resolve `$node.output.field` at runtime. |

### Start-run request body

```json
{
  "workflow_id": "wf-123",
  "input_data": { "key": "value" },
  "tenant_id": "system",
  "correlation_id": "",
  "callback_topic": "",
  "predefined_run_id": null,
  "skip_validation": true,
  "execution_mode": "auto",
  "user_variables": {},
  "metadata": {}
}
```

## Nodes (within a run)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/runs/{run_id}/nodes/{node_id}/execution` | Node execution details. |
| `GET` | `/runs/{run_id}/nodes/{node_id}/schema` | Node input/output schema. |
| `GET` | `/runs/{run_id}/nodes/{node_id}/output` | Node computed output. |
| `GET` | `/runs/{run_id}/nodes/{node_id}/data` | Node input/working data. |
| `PATCH` | `/runs/{run_id}/nodes/{node_id}/data` | Edit node data (e.g. `DATA_EDIT`). |
| `POST` | `/runs/{run_id}/nodes/{node_id}/submit` | Submit input to a pending node. |

### Submit pending node

```json
{ "node_id": "input-node-1", "output_data": { "field1": "value1" } }
```

### Edit node data

```json
{ "node_id": "input-node-1", "working_data": { "key": "new_value" }, "if_match": "<etag>" }
```

`if_match` is the current etag (from `GET .../data`); a stale value is rejected with `412`.

## Tasks (worker-backed nodes)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/runs/{run_id}/tasks/{task_id}` | Get task details. |
| `POST` | `/runs/{run_id}/tasks/{task_id}/complete` | Mark task `COMPLETED`. |
| `POST` | `/runs/{run_id}/tasks/{task_id}/fail` | Mark task `FAILED`. |
| `POST` | `/runs/{run_id}/tasks/{task_id}/rerun` | Re-execute a task (optionally with `new_input`). |

### Complete task body

```json
{ "status": "COMPLETED", "output_reference": "file-id-123", "output_metadata": {}, "idempotency_key": "" }
```

### Fail task body

```json
{ "error": { "message": "..." }, "outputs": {} }
```

These are normally called by the worker executor, but are also useful for manual intervention and tests.

## Catalog & schema

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v2/node-catalog` | List available custom/gateway nodes (when `ENABLE_NODE_CATALOG_V2=true`). |
| `GET` | `/schema` | Resolved variable schema for expressions. |

## Real-time (Socket.IO)

Not REST, but the companion channel. Subscribe to:

- `run:{run_id}` — events for a single run.
- `workflow:{workflow_id}` — events for any run of a workflow.
