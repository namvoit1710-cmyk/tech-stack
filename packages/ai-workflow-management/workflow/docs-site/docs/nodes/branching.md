# Branching & Routing

Branching nodes evaluate data and choose which output port fires. Only edges leaving a fired port are followed.

## CONDITION

**Evaluates a boolean expression and routes to true/false branches.** Runtime: **engine**.

- **Input ports:** `in_default`
- **Output ports:** `true_branch`, `false_branch`

### Config

You provide **either** a structured `data` table **or** a raw `expression` (exactly one).

| Field | Type | Expr | Notes |
|---|---|---|---|
| `data` | array | ✅ | Rows of comparisons (preferred). Each row has the sub-fields below. |
| `expression` | string | ✅ | Raw boolean expression (internal / API-authored alternative to `data`). |

Each `data` row:

| Sub-field | Type | Expr | Notes |
|---|---|---|---|
| `key` | string | ✅ | Left-hand value, e.g. `{{$http-1.output.status_code}}`. |
| `operations` | string | | Comparison operator — `=`, `!=`, `>`, `>=`, `<`, `<=`, `contains`, `starts_with`, `is_empty`. |
| `value` | string | ✅ | Right-hand value or expression, e.g. `200`. |

### Output

| Field | Type | Description |
|---|---|---|
| `result` | boolean | The evaluated boolean. |
| `branch` | string | Which branch fired. |

### Example

```json
{
  "id": "check-status",
  "type": "CONDITION",
  "config": {
    "data": [
      { "key": "{{$http-1.output.status_code}}", "operations": "=", "value": "200" }
    ]
  }
}
```

Wire `true_branch` to the success path and `false_branch` to an error handler.

---

## SWITCH

**Routes execution to one of several branches based on a value.** Runtime: **engine**. Like a `CONDITION` but with many named outcomes plus a `default`.

- **Input ports:** `in_default`
- **Output ports:** one per case + `default`

### Config

| Field | Type | Expr | Notes |
|---|---|---|---|
| `data` | array | ✅ | The case rows (preferred). Each row has the sub-fields below. |
| `expression` | string | ✅ | Raw expression (legacy / API-authored). |
| `cases` | array | | Legacy case list (`value` / `values` / `target`). |
| `default` | string | | Legacy default target. |

Each `data` row:

| Sub-field | Type | Expr | Notes |
|---|---|---|---|
| `key` | string | ✅ | Value to match, e.g. `{{$input.tier}}`. |
| `operations` | string | | Comparison operator (same set as `CONDITION`). |
| `value` | string | ✅ | The value to compare against. |
| `branch` | string | | The branch name / port this case routes to. |

### Output

| Field | Type | Description |
|---|---|---|
| `evaluated_value` | string | The value that was matched on. |
| `matched_case` | string | Which case matched. |
| `branch` | string | The branch that fired. |

### Example

```json
{
  "id": "route-by-tier",
  "type": "SWITCH",
  "config": {
    "data": [
      { "key": "{{$input.tier}}", "operations": "=", "value": "gold",   "branch": "gold" },
      { "key": "{{$input.tier}}", "operations": "=", "value": "silver", "branch": "silver" }
    ]
  }
}
```

If no case matches, the `default` port fires. Connect each named branch port to its handling path.
