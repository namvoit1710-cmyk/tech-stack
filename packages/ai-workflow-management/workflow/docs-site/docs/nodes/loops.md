# Loops

Loops re-execute a body of nodes per item, per count, or until a condition holds. `LOOP_EXIT` and `LOOP_CONTINUE` give you `break` / `continue` control inside the body.

## LOOP

**Iterates over a collection or repeats until a condition is met.** Runtime: **engine**.

- **Input ports:** `in_default`
- **Output ports:** `loop_body` (runs once per iteration), `loop_complete` (fires when the loop finishes)

### Config

| Field | Type | Expr | Notes |
|---|---|---|---|
| `loop_type` | string | | **Required.** `collection` *(default)* / `count` / `condition`. |
| `collection` | string | ✅ | The array to iterate, e.g. `{{$input.items}}`. Shown when `loop_type = collection`. |
| `condition` | string | ✅ | Loop-while expression, e.g. `{{$nodeA.hasMore}}`. Shown when `loop_type = condition`. |
| `max_iterations` | number | | Safety cap. Default `10000`. |
| `mode` | string | | `sequential` *(default)* / `parallel`. |
| `parallelism` | number | | Max concurrent iterations. Shown when `mode = parallel`. |
| `counter_variable` | string | | Name of the index variable. Default `index`. |

### Output

| Field | Type | Description |
|---|---|---|
| `iteration_count` | number | How many iterations ran. |
| `completed` | boolean | Whether the loop finished normally. |
| `break_reason` | string | Why the loop ended (e.g. exhausted, broke, hit max). |

### Inside the body

Within the loop body you can reference the current iteration:

- `{{$loop.index}}` — the iteration index (named after `counter_variable`).
- `{{$loop.item}}` — the current item (for `collection` loops).

### Example

```json
{
  "id": "for-each-order",
  "type": "LOOP",
  "config": {
    "loop_type": "collection",
    "collection": "{{$fetch-orders.output.response_body.orders}}",
    "mode": "sequential",
    "counter_variable": "i"
  }
}
```

Connect `loop_body` to the per-item subgraph and `loop_complete` to whatever runs after the loop.

::: tip Count and condition loops
- `count` loops repeat a fixed number of times (bounded by `max_iterations`).
- `condition` loops repeat **while** `condition` evaluates truthy. Make sure something inside the body changes the condition, or you will hit `max_iterations`.
:::

---

## LOOP_EXIT

**Breaks out of the enclosing loop.** Runtime: **engine**. A dead-end signal node — reaching it ends the current loop early and control proceeds from the loop's `loop_complete` port.

- **Input ports:** `in_default`
- **Output ports:** none (signal node)
- **Config:** none

Place it on a branch inside the loop body — typically after a `CONDITION` — to stop iterating when some condition is met.

```
LOOP ──loop_body──▶ CONDITION ──true──▶ LOOP_EXIT
                          └──false─────▶ (continue processing item)
```

---

## LOOP_CONTINUE

**Skips to the next iteration of the enclosing loop.** Runtime: **engine**. A dead-end signal node — reaching it abandons the rest of the current iteration's body and starts the next one.

- **Input ports:** `in_default`
- **Output ports:** none (signal node)
- **Config:** none

Use it to skip items that do not need the full body:

```
LOOP ──loop_body──▶ CONDITION ──skip?──▶ LOOP_CONTINUE
                          └──no──────────▶ (process this item)
```
