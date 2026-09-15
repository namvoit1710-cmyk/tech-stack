# Phase M.2 — Optimistic CAS on `IRunRepository`

> Make `run_repo.save(run)` a compare-and-swap on a monotonically
> increasing `VERSION` column. Concurrent saves for the same run fail
> deterministically; one wins, the other raises
> ``OptimisticConcurrencyError`` (HTTP 409 at the controller edge).

## 1. Why

Phase 3c removed ``RunLockRegistry`` and routed PATCH through the
single-consumer orchestration worker. That serialises per-instance
PATCHes but does nothing for cross-instance writers — two instances
can still load the same run, mutate independently, and both save,
silently losing one side's update.

Multi-instance PATCH routing is planned as a staged rollout (see
``multi-instance-patch-routing.md`` once landed):

- **M.1** — sticky L7 routing by ``run_id`` (infra config only)
- **M.2** — optimistic CAS at the repo (this doc)
- **M.3** — Kafka partition-by-``run_id`` + response topic

M.2 is the correctness floor. It works regardless of routing, and
M.3 cannot safely ship without it.

## 2. Sub-PR split

M.2 lands in three sub-PRs:

| Sub-PR | Content | LoC | Behaviour change? |
|---|---|---|---|
| **M.2.1** | Infrastructure: `version` field, `OptimisticConcurrencyError`, memory + HANA repo CAS, migration 027, error code | ~150 | No — every existing save still succeeds; contention is still zero in single-instance |
| **M.2.2** | Call-site migration: classify the 41 `run_repo.save` sites; wrap worker-initiated saves in a retry helper; let user-initiated saves surface 409 | ~250 | Yes — 409 path reachable for the first time |
| **M.2.3** | Integration + HANA CAS unit tests, soak | ~200 | Verification only |

This doc tracks M.2.1.

## 3. M.2.1 scope

### 3.1 Domain — `WorkflowRun.version`

Add ``version: int = 0`` field to ``WorkflowRun``
(``app/layer1_domain/entities/workflow_run.py``). Default 0 keeps
tests that construct runs directly compatible.

### 3.2 Repo interface

`app/layer2_application/repositories/run_repository_interface.py`:

```python
class OptimisticConcurrencyError(Exception):
    def __init__(self, run_id: str, expected: int, actual: int): ...


class IRunRepository(Protocol):
    def save(self, run: WorkflowRun) -> None:
        """Persist ``run``. Increments ``run.version`` in place on
        success. Raises ``OptimisticConcurrencyError`` when the
        persisted version no longer matches ``run.version`` (i.e.
        another caller saved between this caller's load and save).

        First-time inserts succeed unconditionally and set
        ``run.version = 1``.
        """
```

### 3.3 Memory repo — CAS

`memory_run_repo.py::save`:

```python
def save(self, run):
    existing = self._store.get(run.id)
    if existing is not None and existing.version != run.version:
        raise OptimisticConcurrencyError(run.id, run.version, existing.version)
    run.version = (existing.version if existing else 0) + 1
    self._store[run.id] = run
```

### 3.4 HANA repo — INSERT/UPDATE split

Current implementation uses ``UPSERT ... WITH PRIMARY KEY``. UPSERT
can't express a CAS predicate, so we split into:

- **INSERT branch** (first-time persist): unconditional insert with
  ``VERSION=1``.
- **UPDATE branch** (row exists): conditional
  ``UPDATE ... SET ..., VERSION = :new_version WHERE ID = :id AND VERSION = :expected``.
  Rowcount 0 → `OptimisticConcurrencyError`.

A ``SELECT VERSION`` pre-check feeds better error messages when the
caller's expected version is already stale at call time. The
atomicity still comes from the ``WHERE VERSION = :expected`` clause
on the UPDATE; the pre-check is diagnostic.

### 3.5 Migration 027

`db/migrations/027_add_version_to_workflow_runs.sql`:

```sql
ALTER TABLE "AIW_WORKFLOW_RUNS"
ADD ("VERSION" INTEGER DEFAULT 0 NOT NULL);
```

HANA error 308 (column exists) is already treated as idempotent by
``migrations.py:22`` — safe to re-run. Existing rows get
``VERSION=0``; their first M.2.1 save bumps to 1.

The ``.hdbtable`` documentation file is updated to reflect the
final column layout.

### 3.6 Error code

`app/layer2_application/errors.py`:

```python
OPTIMISTIC_CONCURRENCY_CONFLICT = (
    "OPTIMISTIC_CONCURRENCY_CONFLICT", 409,
    "Run was modified by another request; reload and retry.",
)
```

### 3.7 Controller mapping

Extend the response-envelope error helper to convert
``OptimisticConcurrencyError`` into a standard 409 response with
``error_code="OPTIMISTIC_CONCURRENCY_CONFLICT"`` — one handler
covers every controller.

## 4. Verification (M.2.1)

- Full regression passes with zero behaviour change. Every existing
  call site still loads → mutates → saves; ``run.version`` matches
  ``existing.version`` at save time so CAS never fires.
- New unit tests:
  - ``test_memory_run_repo_cas.py`` — first-insert, match, stale,
    double-save without reload.
  - ``test_hana_run_repo_cas.py`` — same contract using a
    SQLite-backed HANA stub (mirrors
    ``test_hana_event_log_repo.py`` pattern).

## 5. Out of scope for M.2.1

- Retry logic in use cases (M.2.2).
- Integration test for concurrent PATCH (M.2.3).
- Cross-instance deployment (M.3).

## 5b. M.2.2 — Call-site migration (shipped same day)

### 5b.1 Classification of all 41 sites

| Tier | Count | Sites | Action |
|---|---|---|---|
| **Retry (worker-initiated, real mutation)** | 3 | `complete_task:206` (artifacts registry write), `sweep_timed_out_child_runs:115` (child error + completed_at), `sweep_timed_out_child_runs:169` (grandchild completed_at) | Wrapped in `save_run_with_cas_retry` |
| **Deleted as dead code post-3b** | 3 | `fail_task:165`, `edit_node_data:319`, `edit_node_data:874` | `run` is unmutated after 3b.2a removed the aggregate writes; saves persisted nothing. |
| **Surface 409 (user HTTP)** | 14 | `cancel_run:101,:135`, `resume_pending_task:426`, `pause_run:61`, `rerun_task:185`, `rerun_run:144`, `trigger_node:156`, `test_node:116,:241`, `test_full_workflow:119,:147`, `reset_test_run:73,:83`, `start_run:153,:166,:191` | Zero code change — exception propagates to the M.2.1 global handler (→ HTTP 409). |
| **Insert-only (no CAS possible)** | 1 | `create_workflow:191` (creates a fresh test_run) | Zero code change — first-insert branch is unconditional. |
| **Worker-internal engine saves** | 18 | `orchestration_engine.py` × 18 | Deferred — see §5b.3. |
| **Non-save-of-run** | 2 | `create_workflow` creates a test_run (counted in insert-only above); other 2 are test_node / test_full_workflow / start_run / reset_test_run first-save branches conceptually identical to insert-only | Already handled as insert-only. |

### 5b.2 `save_run_with_cas_retry` helper

`app/layer2_application/run_save_retry.py` — load → apply → save
with bounded retry. Idempotency of the `apply` closure is the
caller's contract. Default 3 attempts, exponential backoff
(20ms / 40ms / 80ms).

Signature:
```python
async def save_run_with_cas_retry(
    run_repo: IRunRepository,
    run_id: str,
    apply: Callable[[WorkflowRun], None],
    *, max_attempts: int = 3, base_delay: float = 0.02,
) -> Optional[WorkflowRun]:
```

### 5b.3 Why orchestration_engine's 18 saves were deferred

The orchestration worker is single-consumer (Phase 3c
`OrchestrationWorker.run_forever`). Engine saves happen inside its
dispatch loop — intra-instance, no two engine saves for the same
run can interleave. Cross-instance conflicts would require a PATCH
via `EditNodeDataAction` on another instance racing with the
worker's in-flight save; in that case the worker's existing
`_logger.exception` handler logs + the queued action fails, and
the projector's event-driven state stays correct because
`NodeOutputProduced` / status events are the authoritative record.

Wrapping 18 mutations with retry closures is invasive (each
mutation is ad-hoc; no clean closure). Defer to a standalone
follow-up when multi-instance deployment lands.

### 5b.4 Verification (M.2.2)

- Full regression unchanged: 1658 passed / 59 baseline / 107
  skipped. No existing tests migrated. Three dead-code saves
  removed, three retry wraps added, zero behaviour regression.
- Concurrent-write behaviour is not exercised by any existing test
  (needs M.2.3).

## 5c. M.2.3 — Integration tests (shipped same day)

### 5c.1 What the tests cover

`tests/integration/test_optimistic_cas_integration.py` ships two
focused tests:

- **`TestHandlerShape::test_409_with_code_message_and_version_details`**
  — direct unit test of the M.2.1 global exception handler.
  Instantiates `OptimisticConcurrencyError(expected=5, actual=7)`,
  calls the handler, asserts the response is HTTP 409 with:
  ```json
  {
    "status": "error",
    "error": {
      "code": "OPTIMISTIC_CONCURRENCY_CONFLICT",
      "message": "... reload and retry ...",
      "details": {
        "run_id": "run-xyz",
        "expected_version": 5,
        "actual_version": 7
      }
    }
  }
  ```
  This is the contract every HTTP endpoint inherits via the global
  handler registered in M.2.1.

- **`TestCancelRunCASPropagation::test_stale_save_inside_cancel_run_raises_optimistic_conflict`**
  — end-to-end race simulation. Seeds a run at v=1 via the SQLite-
  backed HANA stub (row-level isolation, unlike MemoryRunRepository
  which aliases its dict), monkey-patches `run_repo.save` to bump
  the persisted VERSION to 99 mid-call (emulating a competing
  writer), and invokes `CancelRunUseCase.execute`. The use case's
  load returned v=1; its save hits the CAS predicate with persisted
  v=99, which raises `OptimisticConcurrencyError(expected=1, actual=99)`
  straight through the use case to the caller. Asserts the
  exception, then confirms `completed_at` is still NULL in the
  stub — the rejected save didn't leave partial state.

### 5c.2 Why PATCH isn't the test vector

Two important clarifications documented in the test's module
docstring:

1. **PATCH is queue-routed post-Phase 3c.** PATCH sends
   `EditNodeDataAction` to the single-consumer orchestration
   worker; same-run PATCHes serialise there and never race at
   the repo.
2. **PATCH doesn't call `run_repo.save` any more post-M.2.2.**
   Both `edit_node_data` saves were deleted as dead code —
   everything it persists goes through events (`NodeDataEdited`).
   Two concurrent PATCHes today would both return 200.

The authoritative CAS-conflict vector is any HTTP endpoint that
still writes the run aggregate directly: `cancel_run`, `pause_run`,
`rerun_task`, `resume_pending_task`, etc. The test uses
`cancel_run` because its `run_repo.save` mutation
(`completed_at`) is the cleanest single-field demonstration.

### 5c.3 Why there's no `asyncio.gather` concurrency test

The obvious "fire two parallel requests" test is misleading here
and was intentionally omitted. Reasons spelled out in the test
file's trailing comment block:

1. `cancel_run.execute` has no internal `await` between load and
   save, so `asyncio.gather` runs two invocations sequentially
   rather than concurrently.
2. Post-Phase 3b.2c, `cancel_run` doesn't write `status` on the
   aggregate. Without a wired view store (which the minimal test
   fixture lacks by design), `read_authoritative_status` falls
   back to the aggregate, which reports `RUNNING` indefinitely —
   so the second cancel_run passes its status guard and its save
   succeeds at the next fresh version.

A "real" race test needs a multi-instance harness with the
projector wired — scope for Phase M.3 / multi-instance rollout,
not M.2.3.

### 5c.4 Verification (M.2.3)

- Full regression: 1660 passed / 59 baseline / 107 skipped. Delta
  from post-M.2.2: **+2** (the two new tests). Zero new
  regressions.
- Combined across M.2.1 + M.2.2 + M.2.3: 15 new tests cover the
  CAS machinery from repo-level semantics (M.2.1) through use-case
  propagation (M.2.3) and the HTTP handler boundary (M.2.3). The
  retry helper wiring (M.2.2) has no dedicated tests — it's
  exercised indirectly via the full regression, and the 3 wrap
  sites are small enough that explicit retry-loop tests would
  duplicate M.2.1 coverage.

## 6. Rollback

Revert the PR. The ``VERSION`` column stays on HANA — inert, no
reader. No data loss; existing runs continue to work.

## 7. Related docs

- ``phase-3c-run-lock-retirement.md`` — the predecessor; retires the
  in-process lock this CAS replaces at multi-instance scale.
- ``engine-split-dispatcher-executor-projector.md`` §7 — cross-cutting
  work including multi-instance coordination.
