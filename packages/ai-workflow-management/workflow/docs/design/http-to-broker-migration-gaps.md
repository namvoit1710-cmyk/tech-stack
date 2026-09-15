# Moving CP↔executor from HTTP to message broker — what breaks

**Date:** 2026-06-10
**Question:** When task dispatch + result reporting move from HTTP calls to the broker (Kafka / SAP Event Mesh), is anything wrong?

**Headline:** This is an **incomplete migration, not a set of HTTP-vs-broker regressions.** Flipping the worker-executor to full broker mode (`TRANSPORT_MODE=event_mesh`) **hangs every run**, because the executor then reports results *only* on `workflow.results` and **nothing in the control-plane consumes that topic.** Everything else below only bites *after* that consumer is wired.

---

## Scope — which "move to broker" this is about

Two independent switches:

| Switch | Default | Broker value | Affects |
|---|---|---|---|
| `TRANSPORT_MODE` (CP + executor) | `http` | `event_mesh` / `grpc` | how **tasks** dispatch and **results** return |
| `MESSAGING_MODE`/`INFRA_MODE` | `mock` | `local` (Kafka) / `sap` (Event Mesh) | whether a broker exists at all (events, realtime, orchestration queue) |

- **Hybrid (common, fine):** `MESSAGING_MODE=sap` + `TRANSPORT_MODE=http`. Broker carries events/realtime; **tasks + results still go over HTTP** (`HttpTaskDispatcher` POST `/tasks/execute`, `HttpResultReporter` POST `/complete`). The executor *also* publishes to `workflow.results`, but with no consumer it's a harmless no-op. **Runs complete normally.**
- **Full broker (broken):** `TRANSPORT_MODE=event_mesh`. `result_reporter = None` (`bootstrap.py:193`), so results return **only** via `workflow.results`. See §1.

---

## 1. SHOWSTOPPER — `workflow.results` has no consumer in CP

The executor publishes `task.completed` / `task.failed` to `workflow.results` (`EventMeshResultPublisher`, `execute_task_usecase.py:281`). The CP component meant to consume it — `WorkerResultsConsumer` (`worker_results_consumer.py`, docstring: *"replacing the synchronous HTTP /complete callback"*) — is **defined but never instantiated or started**. Grep finds it only in its own module + tests; it is absent from `bootstrap.py` and `main.py`.

Every consumer CP *does* start at lifespan (`main.py`) subscribes elsewhere:

| Consumer | Topic | Purpose |
|---|---|---|
| `ExecutorConsumer` | `node.input` | CP-internal engine-split handler invocation (≠ worker results) |
| `_orchestration_queue` | `orchestration.actions` | `EditNodeDataAction` / PATCH-data |
| `_response_topic_listener` | per-instance response topic | cross-instance `EditNodeData` responses (M.3) |
| `_socketio_consumer` / `_push_gateway_consumer` | realtime event topic | FE push |
| `_catalog_changed_consumer` | `catalog.changed` | cache invalidation |

None handle `task.completed`/`task.failed`. **Result:** in `event_mesh` transport, a task executes, the executor publishes the result, and CP never learns of it → the run sits in `RUNNING` until timeout/forever.

Corroborating evidence this path was never finished: the executor's `TRANSPORT_MODE` doc enumerates only `http|grpc`, yet `main.py:45` branches on `event_mesh`; the engine-split `ExecutorConsumer` emit path is "deliberately limited" (Phase-3 WIP).

**Fix to make full broker mode work at all:** instantiate + `.start()` `WorkerResultsConsumer` in CP lifespan whenever a broker exists (mirror the other consumers). Until then, keep `TRANSPORT_MODE=http`.

---

## 2. Gap-list — what becomes live *once* the consumer is wired

These do not bite today (nothing consumes results), but each is a real correctness/robustness gap that HTTP did not have. Fix alongside the consumer.

### 2.1 Oversize results spuriously fail the node
`workflow.results` carries `output_metadata` = the worker's **full inline output** (HTTP body, DB rows, `mapped_data`, LLM text) — unthinned (see the queue-data audit). The broker caps at ~1 MB.
- **Executor's** `EventMeshBrokerClient.publish_to_topic` has **no size guard** → Event Mesh returns 413 → `publish_result` raises → caught in `execute_task_usecase`'s error handler → emits a spurious `task.failed` (`WORKER_ERROR`). Over HTTP the same result succeeds (no size cap).
- Dispatch (`workflow.tasks`) is **not** at risk: `input` rides as thin `file_ref` pointers (`orchestration_engine.py:1160` — workers materialize via `file_id` in the SDK), so it stays small. The **output** topic is the fat one.

### 2.2 Two different brokers clients, very different robustness
The executor ships an **older/simpler** `EventMeshBrokerClient` than CP's:

| Capability | CP client | Executor client |
|---|---|---|
| Size guard (`_encode_and_validate`, 900 KB) | ✅ raises | ❌ none (→ 413) |
| Retry + backoff on 429/5xx | ✅ | ❌ raises immediately |
| Circuit breaker + DLQ | ✅ | ❌ |
| Async flusher / batching | ✅ | ❌ sync POST |
| Lock-duration TTL control | ✅ | ❌ broker default |

### 2.3 Delivery-semantics asymmetry (sharp inconsistency)
- **Executor task consumer is at-MOST-once:** its poll loop **acks the message even when the handler raised** (`event_mesh_broker_client.py:228-243` — exception caught, then ack runs unconditionally). A task that errors mid-handle is acked → **never redelivered → lost**.
- **CP result consumer (when wired) is at-LEAST-once:** `WorkerResultsConsumer` waits on the handler and skips ack on failure → **redelivery → duplicates.**
- Same system, opposite guarantees. Neither side dedups by `task_id`/`idempotency_key` before executing (`main.py:45 _on_task`).

### 2.4 Idempotency only half-present
On at-least-once redelivery: `CompleteTaskUseCase` **has** a guard (`if task.status == COMPLETED: return idempotent`, `complete_task_use_case.py:105`). `FailTaskUseCase` has **none** — a redelivered `task.failed` re-applies the FAILED transition.

### 2.5 Lost synchronous error propagation + backpressure
- HTTP surfaced 4xx/5xx and the executor's `CONCURRENCY_LIMIT` rejection to the caller, which could retry. Broker dispatch is fire-and-forget: `EventMeshTaskDispatcher._dispatch` **logs and swallows** publish failures (oversize/broker-down) → silent non-dispatch.
- CP async flusher `buffer_policy` defaults to **`drop`** → under load, messages are **silently dropped** when the send buffer fills.

### 2.6 Ordering / redelivery
Short message-lock TTL + a slow handler → redelivery → **out-of-order** events (documented re: the socketio reorder buffer). CP's flusher sorts a batch by enqueue time for in-order publish, but redelivery still reorders. Per-run partition key (`key=run_id`) is set, which helps Kafka.

### 2.7 Operational prerequisites HTTP didn't need
Event Mesh queues need pre-provisioning (fire-and-forget `publish_to_queue` 404s if absent); OAuth2 creds (`EVENT_MESH_*`) must be bound or the broker silently falls back to mock; the consumer is a single daemon poll thread — if `_poll_loop` throws uncaught it **exits and consumption stops permanently** (no supervisor/restart).

---

## 3. Consume-side error handling — what replays, what doesn't, what's DLQ'd

The handle→ack/commit flow per consumer, and which errors cause **replay** (skip ack/commit → redeliver) vs **no replay** (ack/commit anyway) vs **DLQ**.

### Per-component ack/commit behaviour
| Component | Handler error → | Poison (undeserializable) | DLQ on consume failure |
|---|---|---|---|
| CP `KafkaBrokerClient._consume_loop` | skip commit → replay **on rebalance/restart only** (`:271-278`) | commit-past + log (`:233-240`) | none |
| CP `EventMeshBrokerClient._poll_loop` | skip ack → replay after lock-TTL ~60s, **loops forever** if persistent (`:534-547`) | none | none |
| `WorkerResultsConsumer._on_message` | **swallows all exceptions** (`:58-62`) → broker sees success → **always acks → nothing replays** | n/a | none |

The broker clients implement at-least-once (skip-ack-on-exception), but **`WorkerResultsConsumer` defeats it** by catching every exception in `_on_message`. So a transient `complete_task`/`fail_task` failure (CAS 409, DB blip, broker error during emit) is acked and **lost → run hangs** — the same symptom the consumer was wired to prevent.

### Error classification (WorkerResultsConsumer path)
- **Could-not-replay (terminal — correct to ack):** undeserializable payload (Kafka commits past); missing `run_id`/`task_id`; unknown `message_type`; use-case absent from container; `TASK_SUPERSEDED` (generation guard). All are early-returns in `_handle` / use-case results → no raise → acked. **`RUN_NOT_FOUND`/`TASK_NOT_FOUND` are terminal too** — the task is created before dispatch, so not-found means genuinely gone (superseded/cleaned), not a race; replaying would poison-loop.
- **Should-replay (transient — skip ack):** use-case **exceptions** — CAS 409 version conflict, persistence error, broker-publish failure during the emit/enqueue side effects.
- **Passed-to-DLQ:** **nothing.** `publish_to_dlq`/`_dlq_send` fire only from the publish flusher (`event_mesh_broker_client.py:982,989`); there is no consume-side DLQ.

### Why a naive "stop swallowing → re-raise" fix is UNSAFE
Re-raise (so the broker skip-acks and redelivers) collides with the idempotency guards. In both use cases the **terminal status is persisted before the side effects**:
- `complete_task`: guard `:105` (status==COMPLETED) · save `:120` · side effects `:205` (`apply_state_updates_as_files`) + `:239` (`ContinueRunAction` enqueue)
- `fail_task`: guard `:101` · save `:113` · side effects `:133`/`:168`/`:179`

If a side effect (e.g. oversize emit per §2.1, or a broker blip) raises **after** the status save, re-raise → redelivery → attempt 2 reads status already terminal → **guard short-circuits → side effects never run, message now acked.** The run is marked complete/failed but never routed downstream → silent permanent hang. (Untested today: `DummyOrchestrationEngine.apply_state_updates_as_files` is a no-op.)

### Direction (option 1 — re-drive on replay — SHIPPED 2026-06-10)
Option 2 (persist-status-last) was implemented first, then **replaced by option 1** because option 2 is fundamentally `EXECUTOR_MODE=in_process`-only: in `EXECUTOR_MODE=executor` the emits themselves drive the fan-out/completion consumers (which read *persisted* `task.status`), so deferring the save past the emits races there. Option 1 is correct for **both** modes.

1. **Stopped swallowing in `_on_message`** (shipped) — transient exceptions propagate to the broker's skip-ack path → redelivery. Terminal/validation cases still early-return in `_handle` → ack.
2. **Re-drive on replay (option 1, shipped).** Reverted to persisting the terminal status **first** (before the side effects) — safe for both executor modes, since the emit-driven consumers read the persisted status. **Removed the terminal-status short-circuit** in both use cases, so an at-least-once redelivery re-runs the body. Idempotency is provided by the **sinks**, verified:
   - `NodeOutputProduced` carries a **deterministic** `event_id`; the event-log append **silently drops** on a `(run_id, event_id)` collision (returns `False`, never raises) across memory / SQLite (`INSERT OR IGNORE`) / HANA (catches unique-violation `301`). So re-emit is a no-op, **not** a poison-loop.
   - `NodeCompleted`/`NodeFailed` use *random* event_ids (not log-deduped) but fold idempotently into the projector's `completed_nodes` **frozenset** (`run_state_projector.py:106-111`) — duplicate emit is a no-op at run-state level.
   - `ContextUpdated` (emitted alongside `NodeCompleted`) has **no** run-state-projector branch (`run_state_projector.py:335` — unknown event type → state unchanged), so a duplicate is run-state-inert (a redundant realtime/socketio notification only).
   - `store_data` is content-hash keyed (idempotent upload); successor dispatch is edge-deduped (`orchestration_engine.py:398,1482`) and `check_run_completion` is idempotent — so re-enqueuing `ContinueRunAction`/`FailTaskAction` is safe. **Residual:** this is argued from the dedup mechanism, **not** empirically double-delivered through the real engine (the integration fixtures that would exercise it are pre-existing-broken on this branch).
   - **Blast radius:** removing the short-circuit is a **no-op for every non-duplicate delivery** — on first delivery the task is `RUNNING`, so `if status == terminal` never fired. The only behavioral change is on an actual *second* delivery of an already-terminal task → the blast radius is exactly the duplicate-replay tests (`test_{fail,complete}_redrives_on_duplicate`, `test_use_cases::test_complete_task_idempotent`), which is why the full suite need not re-run.
   - The deterministic `event_id` is stable across deliveries (`causation_id=task.id`, `attempt_id=idempotency_key` — both invariant per task), so the dedup actually fires on replay.
   - **Cost of re-drive (rare actual replays only):** duplicate stage-history rows, duplicate `NodeCompleted`/`ContextUpdated` log entries, possibly an orphaned re-upload. All cosmetic/storage — **run-state stays correct.** ⚠️ If the engine-split **drift/parity harness** is run, these duplicate log events may trip it.
   - The `idempotent` result field is now vestigial (nothing reads it); left in place.
3. Bound redelivery (DLQ-after-N) — addresses the **pre-existing** EM poison-loop (affects every EM consumer); track separately.

**Kafka asymmetry footnote:** re-raise restores at-least-once cleanly for **Event Mesh** (per-message ack + lock-TTL redelivery). For **Kafka** it's weak — `_commit_safely` commits the specific message offset, so a later success commits *past* an earlier skipped message → the skipped one never redelivers. EM is the prod path; don't claim Kafka at-least-once.

## Completion checklist (to actually finish the HTTP→broker move)
1. Wire `WorkerResultsConsumer.start()` in CP lifespan when a broker exists. **(unblocks runs)**
2. Add a size guard + retry/DLQ to the executor's broker client, or offload bulk output (see queue-data audit) so results stay < 1 MB.
3. Make `FailTaskUseCase` idempotent; add `task_id` dedup on the executor task consumer.
4. Reconcile delivery semantics: skip-ack-on-failure on the executor side (at-least-once) + idempotent handlers, OR accept at-most-once explicitly.
5. Surface dispatch/publish failures instead of swallowing; reconsider `buffer_policy=raise` for task/result topics.
6. Document queue provisioning + OAuth prerequisites; add consumer-thread supervision.

## Key files
- `bootstrap.py:143-196` (result publisher/reporter wiring), `main.py` lifespan (consumers started)
- `app/layer4_frameworks/messaging/worker_results_consumer.py` (defined, unwired)
- `app/layer4_frameworks/messaging/event_mesh_task_dispatcher.py` (swallows publish errors)
- executor `app/layer4_frameworks/messaging/event_mesh_broker_client.py` (no size guard/retry; auto-ack on failure)
- executor `app/layer2_application/features/execute_task/use_cases/execute_task_usecase.py:281` + `handle_worker_callback_usecase.py` (result publish)
- `complete_task_use_case.py:105` (idempotent) / `fail_task_use_case.py` (not idempotent)
