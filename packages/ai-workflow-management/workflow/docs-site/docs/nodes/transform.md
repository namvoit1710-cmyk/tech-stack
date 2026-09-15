# Transform & Variables

Reshape data inline without a worker: compute values, declare run-scoped variables, and assemble prompts for downstream AI nodes.

## COMPUTE

**Performs in-process data transformation or computation.** Runtime: **engine**. Evaluates an expression and stores the result — no external worker, no network.

- **Input ports:** `in_default`
- **Output ports:** `success`, `failure`

### Config

| Field | Type | Expr | Notes |
|---|---|---|---|
| `expression` | string | ✅ | **Required.** The expression to evaluate, e.g. `len({{$nodeId.output.items}})` or `{{$input.price}} * 1.08`. (Textarea.) |
| `result_key` | string | | Optional key to store the result under, e.g. `total`. |

### Output

| Field | Type | Description |
|---|---|---|
| `result` | object | The computed value (under `result_key` if set). |

### Example

```json
{
  "id": "with-tax",
  "type": "COMPUTE",
  "config": {
    "expression": "{{$input.price}} * 1.08",
    "result_key": "total"
  }
}
```

Read it downstream as `{{$with-tax.output.result}}`. `COMPUTE` is the right tool for arithmetic, counts, string assembly, and small reshaping that does not warrant a worker.

---

## SET_VARIABLE

**Writes named values into the run's variables namespace,** readable downstream as `{{$variables.<name>}}`. Runtime: **engine**. Use it to stash a value once and reference it from many places, including across loop iterations.

- **Input ports:** `in_default`
- **Output ports:** `out_default`
- **Output fields:** none (variables are stored in the run scope, not emitted as node output)

### Config

| Field | Type | Expr | Notes |
|---|---|---|---|
| `variables` | array | | The variables to set. Each item has the sub-fields below. |

Each `variables` item:

| Sub-field | Type | Expr | Notes |
|---|---|---|---|
| `name` | string | | **Required.** Variable name, e.g. `userCount`. |
| `type` | string | | **Required.** `string` / `number` / `boolean` / `object` / `array`. |
| `item_type` | string | | Element type when `type = array` (`string` / `number` / `boolean` / `object`). |
| `value_expression` | string | ✅ | The literal or expression, e.g. `{{$nodeId.totalUsers}}`, `42`, or `"active"`. (Textarea.) |

### Example

```json
{
  "id": "set-count",
  "type": "SET_VARIABLE",
  "config": {
    "variables": [
      { "name": "userCount", "type": "number", "value_expression": "{{$fetch.output.total}}" },
      { "name": "status",    "type": "string", "value_expression": "active" }
    ]
  }
}
```

Anywhere downstream: `{{$variables.userCount}}`, `{{$variables.status}}`.

::: tip
Run-scoped variables also resolve inside branching nodes (`CONDITION`, `SWITCH`) and `COMPUTE` on every execution path. They are the durable place to hold state that outlives a single node's output.
:::

---

## PROMPT

**Holds free text (with `{{...}}` references) to feed a downstream LLM/agent node.** Runtime: **engine**. A `PROMPT` node assembles a string from upstream data; you then wire it into an [`agent`](/reference/workers#agent-worker) `TASK` as the prompt.

- **Input ports:** `in_default`
- **Output ports:** `out_default`

### Config

| Field | Type | Expr | Notes |
|---|---|---|---|
| `content` | string | ✅ | **Required.** The prompt text, with `{{...}}` references resolved at runtime. (Textarea.) |

### Output

| Field | Type | Description |
|---|---|---|
| `content` | string | The fully-resolved prompt text. |

### Example

```json
{
  "id": "summary-prompt",
  "type": "PROMPT",
  "config": {
    "content": "Summarize this ticket for {{$input.customer}}:\n\n{{$fetch-ticket.output.body}}"
  }
}
```

Then feed `{{$summary-prompt.output.content}}` into an `agent` task's `user_prompt`. Keeping the prompt in its own node makes it easy to version, reuse, and review separately from the model call.
