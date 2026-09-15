# Entry & Output

Every workflow starts at a `TRIGGER` and (usually) declares its result with an `OUTPUT` node.

## TRIGGER

**Entry point that starts workflow execution on an event or schedule.** Runtime: **engine**. Every workflow needs exactly one trigger; execution flows out of it.

- **Input ports:** none (it is the start)
- **Output ports:** `out_default`

### Config

| Field | Type | Expr | Notes |
|---|---|---|---|
| `trigger_type` | string | | **Required.** One of `manual` *(default)*, `schedule`, `event`, `webhook`. |
| `schedule_cron` | string | | Cron expression, e.g. `0 9 * * *`. Required & shown when `trigger_type = schedule`. |
| `event_topic` | string | | Topic to subscribe to, e.g. `orders.created`. Required & shown when `trigger_type = event`. |
| `event_filter` | string | ✅ | Filter expression, e.g. `$.data.priority == 'high'`. Shown when `trigger_type = event`. |
| `webhook_path` | string | | Path that receives the webhook, e.g. `/hooks/my-trigger`. Required & shown when `trigger_type = webhook`. |

### Output

| Field | Type | Description |
|---|---|---|
| `trigger_type` | string | How the run was triggered. |
| `triggered_at` | string | Timestamp. |
| `trigger_payload` | object | The event/webhook payload that started the run. |
| `correlation_id` | string | Correlation id for the run. |

### Example

```json
{ "id": "trigger-1", "type": "TRIGGER", "config": { "trigger_type": "manual" } }
```

A scheduled trigger:

```json
{ "id": "trigger-1", "type": "TRIGGER",
  "config": { "trigger_type": "schedule", "schedule_cron": "0 9 * * 1-5" } }
```

::: tip
For a `manual` trigger, the run's `input_data` (from `POST /runs`) is what flows downstream as `{{$input.*}}`. For `event`/`webhook` triggers, the incoming payload is exposed as `{{$trigger-1.output.trigger_payload}}`.
:::

---

## OUTPUT

**Defines and formats the output schema for workflow results.** Runtime: **engine**. The `OUTPUT` node's resolved fields become the run's `output_data`, and — when this workflow is called as a sub-workflow — the values returned to the parent.

- **Input ports:** `in_default`
- **Output ports:** `out_default`

### Config

| Field | Type | Expr | Notes |
|---|---|---|---|
| `instruction` | string | | Free-text description of the output format (textarea). |
| `output_schema` | array | ✅ | The list of output fields. Each item has the sub-fields below. |

Each `output_schema` item:

| Sub-field | Type | Expr | Notes |
|---|---|---|---|
| `key` | string | | **Required.** The output field name, e.g. `total`. |
| `type` | string | | `string` *(default)* / `number` / `boolean` / `object` / `array`. |
| `value` | string | ✅ | The value, typically an expression like `{{$nodeId.output.field}}`. |

### Output

| Field | Type | Description |
|---|---|---|
| `formatted_output` | object | The assembled output object. |

### Example

```json
{
  "id": "out-1",
  "type": "OUTPUT",
  "config": {
    "instruction": "Return the order total and a status flag.",
    "output_schema": [
      { "key": "total",     "type": "number",  "value": "{{$compute-1.output.result}}" },
      { "key": "is_paid",   "type": "boolean", "value": "{{$check-1.output.result}}" }
    ]
  }
}
```

After the run completes, `GET /runs/{run_id}` exposes these as the run's `output_data`, and `GET /runs/{run_id}/artifacts` surfaces any file-backed outputs.

::: tip Sub-workflow contract
When a workflow is invoked by a [`WORKFLOW`](./composition#workflow) node, only the `OUTPUT` node's fields are lifted back to the parent (as `{{$wf-node.output.<field>}}`). Treat `OUTPUT` as the workflow's public return type.
:::
