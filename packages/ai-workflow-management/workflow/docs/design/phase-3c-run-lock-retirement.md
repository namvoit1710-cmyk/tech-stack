# Phase 3c — Run-Lock Registry Retirement

> Replace the in-process per-run `asyncio.Lock` with queue-routed
> serialisation so concurrent PATCH `/runs/{id}/nodes/{node_id}/data`
> operations run one at a time on the single-consumer orchestration
> worker.

## 1. Motivation

`RunLockRegistry` (`app/layer1_domain/services/run_lock_registry.py`,
now deleted) serialised PATCH calls inside one process. Its own
docstring flagged the multi-instance lost-update hazard, with three
listed mitigations: (a) sticky L7 routing by `run_id`, (b) repo-level
optimistic CAS, (c) distributed lock.

Phase 3c takes a fourth option: route PATCH through the orchestration
queue. The queue already has a single in-process consumer
(`OrchestrationWorker.run_forever`), so every action — including
`EditNodeDataAction` — is processed serially by construction. The
lock becomes redundant.

Out of scope: multi-instance coordination. Today the queue +
`ResponseRegistry` are both in-process. Cross-instance PATCH routing
would require a response topic + correlation-id pattern and sticky
routing by `run_id` at the L7 layer — same constraint the prior lock
had.

## 2. What shipped (2026-04-22)

1. **New action** —
   `app/layer2_application/interfaces/orchestration_queue_interface.py::EditNodeDataAction`
   (`action_type="edit_node_data"`, carries `correlation_id` and the
   opaque `command` payload).
2. **Response registry** —
   `app/layer4_frameworks/orchestration_queue/response_registry.py::ResponseRegistry`
   (correlation-id → `asyncio.Future` map with `allocate` / `resolve`
   / `fail` / `release`).
3. **Worker dispatch** — `OrchestrationWorker.__init__` accepts
   `edit_node_data_use_case` + `response_registry` kwargs; the
   dispatch loop routes `EditNodeDataAction` to
   `_dispatch_edit_node_data`, which calls the use case and resolves
   the HTTP caller's future. Exceptions are surfaced via
   `response_registry.fail(...)` so the controller sees them instead
   of silently hanging.
4. **Controller change** —
   `app/layer3_adapters/controllers/restful/v1/run_controller.py::patch_node_data`
   builds the command, allocates a future from the registry, enqueues
   `EditNodeDataAction`, and `await`s the future with a 30-second
   `asyncio.wait_for` timeout. Fallback path: if the container isn't
   wired with both `_orchestration_queue` and
   `_orchestration_response_registry` (tests that skip the worker),
   the controller invokes the use case directly.
5. **Use case cleanup** — `EditNodeDataUseCase.__init__` no longer
   takes `run_lock_registry`; the `self._locks.acquire(...)` /
   `async with lock:` block in `execute` is gone.
6. **Bootstrap rewire** — `RunLockRegistry` import + construction
   removed; `available_dependencies` drops `run_lock_registry`;
   `OrchestrationWorker` is now built after the container so it can
   close over `container["edit_node_data"]` + the fresh
   `ResponseRegistry`. `_orchestration_response_registry` is exposed
   on the container for the controller.
7. **Deletions** —
   `app/layer1_domain/services/run_lock_registry.py` and
   `tests/unit/test_run_lock_registry.py` removed.

## 3. Serialisation guarantee

- `MemoryOrchestrationQueue` is a single `asyncio.Queue` drained by
  one `OrchestrationWorker.run_forever` loop. All actions — not just
  `EditNodeDataAction` — are dispatched sequentially.
- Same-run PATCHes therefore never interleave. Different-run PATCHes
  are also serial, but that's a pre-existing property of the
  single-consumer design, not a regression.
- PATCH latency is now bounded by worker queue depth. Under heavy
  orchestration load (e.g. large fan-out), PATCH waits behind
  `ExecuteNodeAction` / `ContinueRunAction` etc. This trade-off is
  acceptable for the current single-instance deployment profile;
  multi-consumer scaling is a Phase 4 concern.

## 4. Rollback

Revert this PR. `RunLockRegistry` comes back via git. The use case's
constructor signature change is the main risk surface — any tests
that instantiated `EditNodeDataUseCase` with a `run_lock_registry`
keyword argument will need to drop it (one test file migrated in
this PR, see §6 below).

## 5. Verification

- Full regression suite: 1645 passed / 59 pre-existing failed / 107
  skipped. Delta from baseline: −9 passed (exactly the count of
  deleted `test_run_lock_registry` tests). Zero new regressions.
- `tests/integration/test_patch_node_data.py` exercises the PATCH
  flow end-to-end via `TestClient`; failure count unchanged from
  baseline (14 pre-existing failures tracked separately in the
  59-failure bucket, unrelated to 3c).

## 6. Critical files

- `app/layer2_application/interfaces/orchestration_queue_interface.py`
- `app/layer4_frameworks/orchestration_queue/response_registry.py` (new)
- `app/layer4_frameworks/orchestration_queue/worker.py`
- `app/layer2_application/features/edit_node_data/use_cases/edit_node_data_use_case.py`
- `app/layer3_adapters/controllers/restful/v1/run_controller.py`
- `bootstrap.py`

## 7. Related documents

| Doc | Relevance |
|---|---|
| `phase-3b-inline-write-removal.md` | 3b series — finishes aggregate-write removal. 3c is the cleanup that retires the last remaining concurrency guard. |
| Engine split master doc (`engine-split-dispatcher-executor-projector.md`) | §4 Phase 3c scope entry. |
