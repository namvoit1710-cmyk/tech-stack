# Composition

Build big workflows out of smaller, reusable ones with the `WORKFLOW` node.

## WORKFLOW

**Invokes another workflow as a sub-workflow.** Runtime: **engine** (completion mode `child_run`). The node starts a **child run** of another workflow, maps inputs into it, and (optionally) waits for it to finish and binds its output back.

- **Input ports:** `in_default`
- **Output ports:** `success`, `failure`

### Config

| Field | Type | Expr | Notes |
|---|---|---|---|
| `workflow_id` | string | | **Required.** The workflow to invoke, e.g. `wf-xxxxxxxx`. |
| `input_mapping` | object | ✅ | Maps parent values into the child's inputs. Each row is `param_name` → `param_value` (an expression). |
| `wait_for_completion` | boolean | | Wait for the child run to finish before continuing. Default `true`. |
| `timeout_seconds` | number | | Max wait for the child. Default `3600`. |
| `max_depth` | number | | Max nesting depth for sub-workflow chains. Default `10`. *(internal)* |
| `on_failure` | string | | What to do if the child fails. Default `fail`. *(internal)* |
| `output_binding` | object | | How child outputs map back into the parent. *(internal)* |

Each `input_mapping` row:

| Sub-field | Type | Expr | Notes |
|---|---|---|---|
| `param_name` | string | | The child input parameter name, e.g. `orderId`. |
| `param_value` | string | ✅ | The value/expression from the parent, e.g. `{{$trigger-1.output.trigger_payload.id}}`. |

### Output

| Field | Type | Description |
|---|---|---|
| `sub_run_id` | string | The child run's id. |
| `sub_workflow_status` | string | The child run's final status. |
| `output_data` | object | The child's `OUTPUT` fields, lifted into the parent. |

### Example

```json
{
  "id": "run-credit-check",
  "type": "WORKFLOW",
  "config": {
    "workflow_id": "wf-credit-check",
    "input_mapping": {
      "customerId": "{{$input.customer_id}}",
      "amount":     "{{$compute-total.output.result}}"
    },
    "wait_for_completion": true,
    "timeout_seconds": 300
  }
}
```

Downstream, read the child's result as `{{$run-credit-check.output.output_data.<field>}}`.

### Behavior notes

- The child run gets its **own context and depth tracking**; it does not share variables with the parent except via `input_mapping` (in) and `OUTPUT` (out).
- Only the child workflow's [`OUTPUT`](./entry-output#output) fields are returned to the parent — design that node as the sub-workflow's public contract.
- Recursion is bounded by `max_depth` (and the engine's global recursion limit) to prevent runaway nesting.
- With `wait_for_completion: false` the node continues immediately and the child runs independently.
