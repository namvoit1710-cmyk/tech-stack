# Run-Generation Guard

**Status:** Draft
**Author:** Claude (sketch)
**Date:** 2026-04-23
**Motivation:** close late-callback corruption window on test-run re-execute (and any future reset/restart flow).

## 1. Problem

When a test run is re-executed via `POST /workflows/{id}/test/full`, `TestFullWorkflowUseCase` keeps the same `run_id`, resets state in place, and marks pre-existing tasks `CANCELLED`. But:

- Workers already mid-flight do **not** receive a cancel signal (no polling, no interrupt).
- Task callbacks (`complete_task`, `fail_task`) do **not** reject `CANCELLED` tasks — they unconditionally overwrite to `COMPLETED` / `FAILED`.
- `Task` has no notion of which execution generation it belongs to, so late callbacks from execution *N-1* poison execution *N*'s state (`check_run_completion` sees spurious completions; variables get stamped from stale `output_ref`).

Fixing only the callback guard (reject `CANCELLED`) is a band-aid: it doesn't cover new task rows that re-use a deterministic `task_id` after reset, and it doesn't generalise to future restart-from-checkpoint flows.

## 2. Proposal

Introduce a coarse-grained **generation counter** on `WorkflowRun`. Stamp each `Task` with the run's generation at creation. On callbacks, reject tasks whose stamped generation lags the run's current generation.

Key property: `run.generation` is bumped **only** on semantic "new execution start" events — not on every save. It is orthogonal to `run.version` (the CAS version, which bumps on every save).

## 3. Data model

### 3.1 `WorkflowRun`

```python
@dataclass(kw_only=True)
class WorkflowRun(BaseDomainEntity):
    ...
    version: int = 0        # existing — CAS, bumps on every save
    generation: int = 0     # NEW — bumps only on reset/restart
```

### 3.2 `Task`

```python
@dataclass(kw_only=True)
class Task(BaseDomainEntity):
    ...
    run_generation: int = 0   # NEW — stamped at creation from run.generation
```

### 3.3 Persistence

- HANA: `ALTER TABLE WORKFLOW_RUN ADD GENERATION INTEGER DEFAULT 0 NOT NULL;`
- HANA: `ALTER TABLE TASK ADD RUN_GENERATION INTEGER DEFAULT 0 NOT NULL;`
- Memory repos: no change needed (dataclass default picks up).
- HDI schemas in `workflow-control-plane-service/db/hdi/` updated accordingly.

Existing rows default to `0`. A run's first-ever dispatch runs at generation 0; reset bumps to 1, 2, … So "no generation guard" and "first execution of a new run" are indistinguishable from the data alone — that's fine because zero is a valid generation, not a sentinel.

## 4. Dispatch path: stamping tasks

Single choke point: `TaskFactory` (`layer1_domain/services/task_factory.py`).

```python
class TaskFactory:
    @classmethod
    def create_for_run(cls, run: WorkflowRun, node_id: str, ...) -> Task:
        return Task.create(
            run_id=run.id,
            node_id=node_id,
            run_generation=run.generation,   # NEW
            ...
        )
```

All engine + dispatcher task creation MUST go through the factory. Audit any `Task(...)` direct construction outside tests and migrate. (Grep `Task(run_id=` for offenders.)

**Loop / parallel fan-out:** all tasks in a single execution share one generation. The stamp happens at `TaskFactory.create_for_run` time; `dataclasses.replace` paths (like `Task.with_pushed_scope`) preserve the field.

**Retry (automatic engine-driven):** creates a new Task row with the same `run.generation`. Correct — we only want to reject cross-generation, not cross-retry.

## 5. Reset / restart paths: bumping generation

Bump must be atomic with the state-clear and persist via CAS-save. Two callers today:

### 5.1 `TestFullWorkflowUseCase` (`test_full_workflow_use_case.py:122-147`)

```python
else:
    # Reuse existing test run — bump generation, clear stale state
    test_run.generation += 1                        # NEW — must precede task cancel
    test_run.context.dag_info = dag_info
    test_run.test_mode = "full"
    test_run.context.completed_nodes.clear()
    ...
    for stale_task in self.task_repo.list_by_run(test_run.id):
        stale_task.status = TaskStatus.CANCELLED
        stale_task.completed_at = ...
        self.task_repo.save(stale_task)
```

Ordering matters: bump generation **before** the stale-task loop so that any callback arriving mid-loop is already rejectable by generation check (doesn't rely on `CANCELLED` being observed).

The subsequent `run_repo.save(test_run)` uses CAS. Two concurrent re-executes → one wins, the other gets 409 at the controller. We get free serialisation on reset.

### 5.2 `CancelRunUseCase` + future restart

Current `CancelRunUseCase` does not restart — it only cancels. No generation bump needed there. But any future "retry-from-failure" or "resume-from-checkpoint" flow MUST bump generation. Document this in `CLAUDE.md` under the Reset section.

### 5.3 `ResetTestRunUseCase` (`reset_test_run_use_case.py`)

Same bump as 5.1. Should also iterate + cancel stale tasks (check: does it today?). If not, add for parity.

## 6. Callback path: validation

Two entry points: `CompleteTaskUseCase._complete_task` and `FailTaskUseCase._fail_task`.

### 6.1 Guard

Insert after task load, before status-idempotency check:

```python
task = self.task_repo.get_by_id(req.task_id)
if not task:
    return CompleteTaskResult(error=..., error_code=ErrorCode.TASK_NOT_FOUND.code, ...)

# NEW — reject late callbacks from a superseded execution generation
if task.run_generation < run.generation:
    self.logger.warning(
        "Rejecting late task callback from superseded generation",
        task_id=req.task_id,
        task_generation=task.run_generation,
        run_generation=run.generation,
        run_id=run.id,
    )
    self._metrics.increment("task.callback.superseded_generation", tags={"handler": "complete"})
    return CompleteTaskResult(
        completed=False,
        error=ErrorCode.TASK_SUPERSEDED.default_message,
        error_code=ErrorCode.TASK_SUPERSEDED.code,
        error_details={"task_generation": task.run_generation, "run_generation": run.generation},
    )

if task.status == TaskStatus.COMPLETED:
    return CompleteTaskResult(completed=True, status="COMPLETED", idempotent=True)
```

### 6.2 New error code

`ErrorCode.TASK_SUPERSEDED` → HTTP 409 via existing error-handler middleware. Worker-executor sees 409 from control-plane → treat as terminal (do not retry), log, drop. Add to `handle_worker_callback_usecase` response-code switch.

### 6.3 Strict equality vs. `<`

Use `<`, not `!=`. A `task.run_generation > run.generation` case can only happen if a task row was written with a generation the run hasn't yet been bumped to — indicates a bug (task stamped before run.save succeeded). Treat `>` as an error and log-louder, but still reject.

## 7. Edge cases

### 7.1 Child workflows

Child workflow runs are separate `WorkflowRun` rows (parent-child via `parent_run_id`). They have their own `generation`. When the parent's generation bumps:

- Existing child runs keep their own generation — their tasks still pass *their* own guard.
- But the parent no longer "owns" them logically: `context.child_runs` is cleared on reset, so completion events from stale child runs won't be routed anywhere useful by the parent.
- **Open question:** should parent-reset cascade-cancel child runs (mark child `run.status = CANCELLED`)? Probably yes, to prevent orphaned compute. Out of scope for this doc but flag for follow-up.

### 7.2 Loops and parallel

All iterations / branches dispatched during one execution share one generation. Mid-execution iterations are not "new generations" — correct.

### 7.3 First-ever execution

Fresh runs default to `generation=0`. First dispatch stamps tasks with `0`. Callbacks check `task.run_generation (0) < run.generation (0)` → false → pass. Works.

### 7.4 Deterministic task IDs

Phase 0 det-id audit means some task IDs are derived from `(run_id, node_id, ...)`. After reset, a re-dispatched task **may collide with a CANCELLED row's ID**. With the guard:

- New row: INSERT would conflict with existing CANCELLED row. Need a task-save policy:
  - Option A: `task_repo.save` does UPSERT; new row overwrites old (including `run_generation`). Acceptable — worker callbacks with the stale generation get rejected before any mutation.
  - Option B: Soft-delete old tasks (add `superseded_at` column) and new row gets a new ID. Cleaner audit trail but larger change.
- Recommend Option A for this sketch. The guard makes it safe.

### 7.5 Interaction with Task-repo CAS

Task repo is currently non-CAS (UPSERT). This sketch does **not** fix that — it's orthogonal. The generation guard closes the late-callback window even without Task CAS, because rejection happens before any write. Task CAS is a separate future improvement.

## 8. Migration

1. **Schema**: add columns with `DEFAULT 0 NOT NULL`. Existing rows fill in-place.
2. **No data backfill needed**: 0 is a valid generation.
3. **Deploy order**:
   - Deploy code with field defined, stamped, but **guard disabled** via flag (see §9). Ensures all new tasks have `run_generation=0` persisted.
   - Run for soak period (e.g., 24h).
   - Flip guard flag on.
4. **Rollback**: flip flag off. Code is safe with mixed-generation data.

## 9. Rollout — feature flag

`GENERATION_GUARD_MODE` env var, tri-state (mirrors PROJECTOR_READ_MODE pattern):

- `off` — skip generation check in callbacks; stamp-on-create still active.
- `shadow` — check and log + metric on mismatch, but do not reject. Lets us observe rate of would-be rejections in production without blocking workers.
- `on` — enforce rejection.

Default `off` for the first release, promote to `shadow` in a follow-up, then `on`.

Soak signal for promotion: `task.callback.superseded_generation` metric should be ≈0 for non-test runs; spikes only on test-run re-execute.

## 10. Testing strategy

### 10.1 Unit tests

- `TaskFactory.create_for_run` stamps `run_generation` from run. Generation 0, 5, 42.
- `CompleteTaskUseCase` with `task.run_generation < run.generation` → returns `TASK_SUPERSEDED`, does not save, does not emit `task.completed`.
- `FailTaskUseCase` with superseded generation → same.
- `TestFullWorkflowUseCase` bumps generation on reset; first-time test run starts at 0 (or 1 — pick and document).

### 10.2 Integration tests

- **Race test**: dispatch task T1 on worker; mid-execution, call `POST /test/full` again; worker posts callback with T1; expect 409 / `TASK_SUPERSEDED`; expect new run (generation N+1) not contaminated.
- **Idempotency preserved**: same-generation retry callback still returns `idempotent=true` for already-completed tasks.
- **Deterministic-id collision**: re-execute with det-id task; verify new task overwrites old row and stamp is bumped.

### 10.3 Regression

Run full suite under `GENERATION_GUARD_MODE=on` to verify no legitimate callback gets rejected. Phase 6 uniform-execution tests are the key coverage.

## 11. Observability

Metrics (prefix `workflow.`):

- `task.callback.superseded_generation` — counter, tags `{handler: complete|fail, infra_mode}`.
- `run.generation.bumped` — counter, tags `{cause: test_full_reset|reset|future_retry}`.

Log fields (structured): `run_id`, `run_generation`, `task_generation`, `task_id`, `handler`.

Grafana: add panel "Superseded callback rate" beside existing CAS-conflict panel.

## 12. Not in scope

- Cascade-cancel of child workflow runs on parent reset (§7.1).
- Worker-side cancellation signalling (out-of-band interrupt to in-flight workers).
- Task-repo CAS conversion (§7.5).
- Production run restart / retry-from-failure (no such flow exists yet; when it does, bump generation per §5.2).

## 13. Open questions

1. Should `run.generation` be bumped on `TestFullWorkflowUseCase` creation of a brand-new test run (to 1), or stay 0? Recommend **stay 0** — bump is specifically a "supersede previous execution" signal; first execution has nothing to supersede.
2. Should `cancel_run` bump generation? No for now — cancel is terminal, no re-execution. Revisit when restart flow is designed.
3. `ResetTestRunUseCase` vs `TestFullWorkflowUseCase` — consolidate reset logic into a shared `_reset_run_state` helper to keep bump + clear + cancel-tasks atomic in one place. Recommended.
4. Size of history we keep of superseded tasks — do we prune CANCELLED rows older than N days? Storage concern, not correctness. Defer.

## 14. Phasing

- **G.1** — schema migration + field addition + TaskFactory stamp + bump in reset flows. `MODE=off`. Zero behavioral change.
- **G.2** — guard in `complete_task` + `fail_task`, `TASK_SUPERSEDED` error code, worker-executor treats 409 as terminal. `MODE=shadow`. Observe.
- **G.3** — flip `MODE=on`. Remove `off` after one release.
- **G.4** — consolidate reset logic per §13-Q3; add cascade child cancel per §7.1 (separate design doc).

---

## Appendix A — File touch list

| File | Change |
|---|---|
| `layer1_domain/entities/workflow_run.py` | add `generation: int = 0` |
| `layer1_domain/entities/task.py` | add `run_generation: int = 0` |
| `layer1_domain/services/task_factory.py` | stamp `run_generation=run.generation` |
| `layer2_application/features/test_full_workflow/use_cases/test_full_workflow_use_case.py` | bump before reset loop |
| `layer2_application/features/reset_test_run/use_cases/reset_test_run_use_case.py` | bump, align with test_full |
| `layer2_application/features/complete_task/use_cases/complete_task_use_case.py` | guard |
| `layer2_application/features/fail_task/use_cases/fail_task_use_case.py` | guard |
| `layer2_application/errors.py` | add `ErrorCode.TASK_SUPERSEDED` → 409 |
| `layer4_frameworks/persistence/hana/hana_run_repo.py` | persist `generation` column |
| `layer4_frameworks/persistence/hana/hana_task_repo.py` | persist `run_generation` column |
| `db/hdi/**` | schema DDL |
| `worker-executor-service/.../handle_worker_callback_usecase.py` | treat 409 `TASK_SUPERSEDED` as terminal; log; do not retry |
| `tests/unit/test_task_factory.py` | stamp test |
| `tests/unit/test_complete_task.py` | guard test |
| `tests/unit/test_fail_task.py` | guard test |
| `tests/integration/test_test_full_workflow_reexecute.py` | race test |
| `CLAUDE.md` | document reset invariant |

Estimated: ~500–700 LOC production, ~300 LOC tests.
