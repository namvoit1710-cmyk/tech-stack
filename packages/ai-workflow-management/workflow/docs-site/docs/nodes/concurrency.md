# Concurrency

Run several branches at once with `PARALLEL`, then synchronize them back together with `MERGE`.

## PARALLEL

**Executes multiple branches concurrently.** Runtime: **engine**. Fans out into independent branches that run at the same time.

- **Input ports:** `in_default`
- **Output ports:** one per branch (created dynamically as you wire edges out)

### Config

| Field | Type | Expr | Notes |
|---|---|---|---|
| `branches` | array | | The branch definitions. Managed by the editor as you draw outgoing edges; each branch may carry an optional guard condition. *(internal)* |

### Output

| Field | Type | Description |
|---|---|---|
| `branches_total` | number | How many branches were fanned out. |
| `branches_completed` | number | How many finished. |
| `branches_failed` | number | How many failed. |
| `all_succeeded` | boolean | True if every branch succeeded. |

### Example

```
            ┌─▶ TASK (notify email)
PARALLEL ───┤
            └─▶ TASK (notify slack)
```

Wire one outgoing edge per concurrent path. Each branch may optionally have a guard condition so it only runs when relevant.

---

## MERGE

**Waits for parallel branches to complete before continuing.** Runtime: **engine**. The join counterpart to `PARALLEL` (and any fan-out). It collects incoming branches and fires once its quorum strategy is satisfied.

- **Input ports:** dynamically connected from the upstream branches
- **Output ports:** `out_default` (exactly one)

### Config

| Field | Type | Expr | Notes |
|---|---|---|---|
| `merge_strategy` | string | | **Required.** `all` *(default, wait for every branch)* / `any` *(first to finish)* / `n_of_m` *(wait for N)*. |
| `wait_count` | number | | How many branches to wait for. Shown when `merge_strategy = n_of_m`. |
| `timeout_seconds` | number | | Give up after this long. Default `3600`. |
| `on_branch_failure` | string | | `fail` *(default — a failed branch fails the merge)* / `partial` *(proceed with what completed)*. |
| `inputs` | array | | Declared inputs to collect (`source` / `key` / `required`). *(internal)* |
| `defaults` | object | | Default values for missing inputs. *(internal)* |

### Output

| Field | Type | Description |
|---|---|---|
| `branches_received` | number | How many branches arrived. |
| `completed` | boolean | Whether the merge completed (quorum met). |
| `timed_out` | boolean | Whether it timed out waiting. |

### Example

```json
{
  "id": "join",
  "type": "MERGE",
  "config": {
    "merge_strategy": "n_of_m",
    "wait_count": 2,
    "timeout_seconds": 600,
    "on_branch_failure": "partial"
  }
}
```

### Behavior notes

- **Skip propagation** — a `MERGE` successor is skipped only if *all* of its predecessors were skipped. As long as one branch produced a real result, the merge proceeds.
- **Timeouts** — if `timeout_seconds` elapses before quorum, the merge stops waiting and reports `timed_out`.
- Use `all` when every branch must finish, `any` for a race, and `n_of_m` for "good enough" quorum.
