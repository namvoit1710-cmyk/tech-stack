# Integration

Nodes that reach into other systems from inside the engine.

## APPROVAL_FLOW

**Fetches an approval process from the governance API and transforms it into a graph.** Runtime: **engine**. Given a change-request id, it retrieves that request's approval process and returns it as nodes and edges you can visualize or route on.

- **Input ports:** `in_default`
- **Output ports:** `success`, `failure`

### Config

| Field | Type | Expr | Notes |
|---|---|---|---|
| `change_request_id` | string | ✅ | **Required.** The change request to fetch, e.g. `{{$nodeId.output.change_request_id}}`. |

### Output

| Field | Type | Description |
|---|---|---|
| `nodes` | object | The approval-process nodes. |
| `edges` | object | The approval-process edges. |
| `change_request_id` | string | Echo of the request id. |

### Example

```json
{
  "id": "fetch-approval",
  "type": "APPROVAL_FLOW",
  "config": {
    "change_request_id": "{{$input.change_request_id}}"
  }
}
```

On success, downstream nodes can read the approval graph from `{{$fetch-approval.output.nodes}}` and `{{$fetch-approval.output.edges}}`. Route the `failure` port to handle a missing or unauthorized change request.

::: tip
This node calls out to a governance service, so it depends on that service being configured and reachable from the control plane. Like other outward-facing nodes it exposes a `failure` port — wire it so an integration error does not fail the whole run.
:::
