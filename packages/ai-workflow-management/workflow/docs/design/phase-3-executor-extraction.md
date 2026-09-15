# Phase 3 — Executor Extraction

> Package node-handler execution as a standalone Executor service.
> Dispatcher emits `node.input` on the bus → Executor consumes →
> invokes the appropriate handler → emits `node.output` /
> `task.completed` / `task.failed`. Side-effecting handlers
> (DATA_EDIT, HITL resume) gain an outbox layer so redelivery is
> safe.

## 1. Why

Today all 13 node handlers run in-process inside the control-plane
pod. The single orchestration worker consumes
`EditNodeDataAction` / `ExecuteNodeAction` from the orchestration
queue and invokes handlers synchronously on the asyncio loop. Three
consequences:

- **CPU blast radius.** A long-running handler (LOOP with
  thousands of iterations, heavy COMPUTE, slow DATA_EDIT) blocks
  every other run on the same pod — including the HTTP / Socket.IO
  paths sharing the loop.
- **Scale-out ceiling.** Adding pods helps only if M.1 sticky
  routing directs the right `run_id` to the right pod. Handlers
  can't be scaled independently of the API surface.
- **Deploy coupling.** Any handler change forces a full
  control-plane redeploy. Iteration cost on handler work is
  inflated by the blast radius of touching the API pod.

Phase 3 splits execution out of the control plane. After Phase 3:

- The control plane hosts **HTTP + Socket.IO + Dispatcher +
  Projector**. Handler code still lives in the repo but is no
  longer called in-process.
- The Executor is a **separate deployment** (separate container,
  horizontal scaling). It consumes `node.input`, invokes the
  handler, emits `node.output` / task-terminal events.
- The orchestration queue (`EditNodeDataAction`) and in-process
  `OrchestrationWorker` stay temporarily for the PATCH path. Full
  retirement of both is a follow-up (master doc §4.3 step 2).

This matches the target diagram in
`engine-split-dispatcher-executor-projector.md` §4.3.

## 2. Non-goals

- **Relocating worker-executor service.** Task-RPC (workflow ↔
  external workers) is already a separate service. Phase 3 sits
  **above** it — the new Executor calls the existing
  worker-executor via HTTP/gRPC for TASK nodes, same as today.
- **BUILTIN/TASK split into separate Executor pools.** Master doc
  §4.3 step 2: "Start unified; split later if one handler family
  dominates CPU." Phase 3 ships one Executor binary with all 13
  handlers registered.
- **Replacing deterministic event-id generation.** Phase 0 wired
  `deterministic_event_id()` everywhere; Phase 3 just relies on
  it for dedupe. No new id scheme.
- **Replacing the orchestration queue with bus-only routing.**
  PATCH through `EditNodeDataAction` stays. Phase 3's scope is
  the `ExecuteNodeAction` code path (BUILTIN+TASK execution), not
  the DATA_EDIT PATCH path — even though DATA_EDIT's side effects
  are moved behind the outbox.
- **Cross-event-log transaction.** The outbox is **not** a
  distributed-transaction protocol. It's a local-transaction
  pattern: side-effect intent + event append commit together in
  the event log, then an async dispatcher performs the real
  side effect.

## 3. The outbox pattern — primer

### 3.1 The problem

Under Kafka at-least-once semantics (and Phase 3 requires exactly
that — the current `enable.auto.commit=True` + commit-before-callback
is effectively at-most-once and must be fixed as a prerequisite), any
`node.input` delivery can be **replayed** on consumer-group
rebalance, Kafka retention overlap, or Executor crash+restart.

Pure-output handlers (CONDITION, SWITCH, COMPUTE, TRIGGER, PARALLEL,
MERGE, LOOP, LOOP_EXIT, LOOP_CONTINUE, WORKFLOW, INPUT,
HUMAN_ACTION, FILE_UPLOAD on its evaluation-only branch) are safe
under replay: same input → same `NodeHandlerResult` → same event
ids (deterministic) → log dedupes at the `(run_id, event_id)`
level. No special machinery.

Side-effect handlers are NOT safe under replay:

| Handler | Side effect | Replay consequence |
|---|---|---|
| DATA_EDIT | `file_service.upload_file(bytes)` creates a new `file_id` | Second upload = orphan file, possibly wrong mutation applied twice |
| DATA_EDIT | `file_service.delete_file(fid)` GCs orphans | Second delete is a no-op but wastes an RPC |
| HITL resume (`resume_pending_task`) | `stage_recorder.record(PENDING→RUNNING)` audit row | Duplicate audit rows |
| HITL resume (`resume_run`) | `queue.enqueue(ResumeRunAction)` | Duplicate task re-dispatch if race with first action |

"Already done" needs a durable record keyed by event id.

### 3.2 The pattern

Traditional outbox: **write side-effect intent + update business
state in the same local transaction; an async dispatcher reads
the intent, performs the side effect with idempotency keys,
marks the intent done.**

Adapted to this codebase:

1. Handler runs. Instead of calling `file_service.upload_file`,
   it appends an **OutboxEntry** to the event log **in the same
   write** as the `NodeOutputProduced` / `TaskCompleted` event.
   One atomic log append, no distributed tx.
2. An **OutboxDispatcher** background task reads unresolved
   entries (ordered by sequence), performs the real side effect
   using the entry's `idempotency_key`, and appends an
   `OutboxEntryResolved` event with the result (e.g. the real
   `file_id`).
3. Callers that need the side-effect's output (e.g. DATA_EDIT
   needs the uploaded `file_id` to register the new artifact)
   observe the resolution via the event log.

Replay is safe because:
- Log append dedupes on `(run_id, event_id)` (Phase 4b.1 existing
  invariant).
- OutboxDispatcher dedupes on the side-effect's
  `idempotency_key` — file-service's `PUT /upload?idem=...` or a
  local "has this entry been resolved?" check before the RPC.

### 3.3 Where the outbox lives

**Inside the event log.** A new event type `OutboxRequested`
carries `{outbox_id, kind, payload, idempotency_key}`. A
`OutboxResolved` event records the outcome. Same
`AIW_EVENT_LOG` table, same replay invariants, same
`(run_id, event_id)` dedupe. No new repository.

Advantages over a separate `AIW_OUTBOX` table:
- Log order is causation order; no need to synchronise two
  persistences.
- Projector already folds the log; adding "what's pending?"
  becomes a reducer branch, not a new read path.
- Matches the existing snapshot / replay story (Phase 4).

Disadvantage: the event log grows with every side effect. Mild —
compared to `NodeOutputProduced` + `TaskCompleted` volume,
outbox events are rare (DATA_EDIT and HITL resume only).

## 4. What ships

Seven sub-tasks, sized for independent PRs. All except 3.3 land
behind `EXECUTOR_MODE=in_process` (the current behaviour); 3.5
flips shadow mode; 3.7 is ops-only.

| Sub | Deliverable | LoC | Behaviour change |
|-----|-------------|-----|-----------------|
| **3.1** | Outbox design doc `phase-3-outbox-pattern.md`; formalises event shapes, idempotency-key scheme, dispatcher semantics | 0 | None — doc only |
| **3.2** | `IOutboxRepository` + memory/HANA impls; `OutboxDispatcher` async task; `OutboxRequested`/`OutboxResolved` events in `events.py`; projector reducer branches | ~450 | None when no handler emits outbox entries |
| **3.3** | DATA_EDIT migration — `edit_node_data_use_case.py` file-service calls routed through outbox (six call sites — head/query/download/upload/delete + rerun-dispatch) | ~300 | Observable: second PATCH retry no longer creates orphan files |
| **3.4** | HITL resume migration — `resume_pending_task` + `resume_run` use cases; the `stage_recorder.record()` and `queue.enqueue(ResumeRunAction)` calls become outbox entries | ~180 | Observable: idempotent re-issue of resume API |
| **3.5** | `ExecutorConsumer` — new broker-subscribed consumer on `node.input`; invokes handler registry; emits output events. Feature flag `EXECUTOR_MODE={in_process,shadow,executor}` default `in_process`. Parity comparator for shadow mode | ~600 | Shadow mode runs both paths and diffs; authoritative mode deletes the engine's in-process invocation | 
| **3.6** | Kill-a-replica chaos test — integration test that SIGKILLs the Executor mid-handler and verifies redelivery → idempotent completion | ~250 | Test-only |
| **3.7** | Standalone deployment — new container image `wio-executor`, k8s Deployment / CF manifest, HPA, separate log/metrics namespaces. Docs update | ~200 | Observable in prod: Executor pods appear alongside control-plane pods |

**Total:** ~2000 LoC + deployment assets. Biggest phase since
1b. Risk profile matches master doc ("high").

## 5. Prerequisites — fix before the Executor starts consuming

### 5.1 Kafka acknowledgement semantics

Today `kafka_broker_client.py` sets `enable.auto.commit=True` and
commits offsets **before** the callback runs. Effective semantics:
**at-most-once** — a crashed callback loses the message.

For the Executor, we need genuine at-least-once:
- `enable.auto.commit=False`.
- Commit after the callback returns successfully.
- On callback exception, do NOT commit; Kafka redelivers after
  the consumer-group session-timeout expiry.

This is a one-file change in `kafka_broker_client.py`. Land it as
a preamble PR (Sub 3.0 in execution order). The dispatcher consumer
currently tolerates either semantic because dispatcher events are
idempotent, but the Executor cannot.

### 5.2 Event-Mesh equivalent

Event Mesh uses explicit `acknowledge()` already
(`event_mesh_broker_client.py`). Audit the current code path
calls `ack()` only on callback success — if it acks on receipt,
apply the same fix. Verify by reading the broker client and the
single integration test (`test_event_mesh_broker_client.py`).

### 5.3 Deterministic event-id audit for side-effect handlers

Phase 0 audited event ids across the engine. Re-verify that
DATA_EDIT and HITL resume emit deterministic ids — the outbox
dedupe relies on `(run_id, outbox_id)` being stable across
replays. If `outbox_id` is derived from `(task_id, op_index)` or
similar, it's already deterministic; otherwise add derivation.

## 6. Sub-task 3.1 — Outbox design doc

**File:** `docs/design/phase-3-outbox-pattern.md`

Self-contained doc covering:

### 6.1 Event shapes

```python
@dataclass(frozen=True)
class OutboxRequested(DomainEvent):
    outbox_id: str           # deterministic: {event_id}:{op_index}
    kind: str                # "file_upload" | "file_delete" | "queue_enqueue" | "stage_record"
    payload: dict            # kind-specific; e.g. {"filename": ..., "content_hash": ...}
    idempotency_key: str     # sent to downstream (file-service accepts ?idem=X)

@dataclass(frozen=True)
class OutboxResolved(DomainEvent):
    outbox_id: str           # correlates back to OutboxRequested
    outcome: str             # "ok" | "failed"
    result: dict             # kind-specific; e.g. {"file_id": "f_abc"}
    error: str | None
```

### 6.2 Kinds + idempotency-key schemes

| kind | Payload | Idempotency key | Target |
|---|---|---|---|
| `file_upload` | `{filename, content_hash}` | `sha256(content) + file_service_namespace` | `file_service.upload_file` |
| `file_delete` | `{file_id}` | `file_id` (idempotent by nature) | `file_service.delete_file` |
| `queue_enqueue` | `{action_type, action_payload}` | `outbox_id` + queue's own dedupe | `orchestration_queue.enqueue` |
| `stage_record` | `{kind, payload}` | `outbox_id` | `stage_recorder.record` |

**`content_hash`** is the payload bytes' sha256 **before** upload.
The dispatcher, on re-processing a replayed `OutboxRequested`,
first asks the file-service "do you already have a file with this
content hash?" (via an idempotency-key header — `file_service`
supports this today via the `idempotency_key` parameter on
`upload_file`). If yes, the existing `file_id` is returned. If no,
upload.

### 6.3 Dispatcher semantics

```
loop forever:
    entries = event_log.list_unresolved_outbox_requests()
    for entry in entries:
        try:
            result = perform(entry.kind, entry.payload, entry.idempotency_key)
            event_log.append(OutboxResolved(outbox_id=entry.outbox_id, outcome="ok", result=result))
        except RetryableError as e:
            if entry.attempts >= MAX_ATTEMPTS:
                event_log.append(OutboxResolved(outbox_id=entry.outbox_id, outcome="failed", error=str(e)))
            else:
                record_attempt(entry); sleep(backoff(entry.attempts))
        except PermanentError as e:
            event_log.append(OutboxResolved(outbox_id=entry.outbox_id, outcome="failed", error=str(e)))
```

- **`list_unresolved_outbox_requests`**: event-log query:
  `OutboxRequested` events for which no `OutboxResolved` with the
  matching `outbox_id` exists. Implemented as a projector reducer
  branch (§6.5).
- **Retry budget**: `MAX_ATTEMPTS=5` default, exponential backoff
  with jitter. Configurable per-kind via settings.
- **DLQ**: no separate DLQ topic in Phase 3; failed entries are
  visible via `OutboxResolved(outcome="failed")` and surfaced on
  a dedicated metrics counter. An ops dashboard can filter the
  projector's `pending_outbox` view.

### 6.4 Consumer side — "waiting on outbox"

For DATA_EDIT specifically: the use case needs the uploaded
`file_id` to update the artifact tree in the node's working copy.
The flow:

1. Handler builds the mutated CSV bytes, appends
   `OutboxRequested(kind="file_upload", ...)` to the log.
2. Handler appends `NodeOutputProduced` **with a placeholder
   `__pending_file_ref__` marker** referencing `outbox_id`.
3. HTTP response to the PATCH caller returns 202 Accepted with
   the `outbox_id` so the client can poll. (Or 200 with a
   `pending` status — see §6.6.)
4. OutboxDispatcher eventually appends `OutboxResolved` with the
   real `file_id`.
5. Projector, on folding `OutboxResolved`, rewrites the
   placeholder in the node's variables to the real
   `__file_ref` with `file_id`.

Alternative (simpler, stays synchronous for now): the handler
blocks on a short-timeout wait for `OutboxResolved` before
returning, using the same in-process `ResponseRegistry` pattern
Phase 3c uses for PATCH. Under normal operation the file upload
is sub-second; under replay the dispatcher may have already
resolved the request, in which case the wait is a single event-log
read. Adopted as the **default**; async-placeholder model is
deferred to a later phase only if wait-latency becomes a problem.

### 6.5 Projector reducer branch

`run_state_projector.py::apply_event` gets two new branches:

```python
elif event_type == "OutboxRequested":
    # Track unresolved requests in a lightweight pending-dict.
    pending = dict(state.pending_outbox or {})
    pending[payload["outbox_id"]] = {
        "kind": payload["kind"],
        "requested_at": envelope.timestamp,
    }
    return replace(state, pending_outbox=pending)

elif event_type == "OutboxResolved":
    pending = dict(state.pending_outbox or {})
    pending.pop(payload["outbox_id"], None)
    return replace(state, pending_outbox=pending)
```

`ProjectedRunState` gains a `pending_outbox: dict[str, dict]`
field. `SCHEMA_VERSION` bumps from 1 to 2 (Phase 4.2) —
existing snapshots auto-invalidate on next threshold crossing.

### 6.6 HTTP semantics

PATCH `/runs/{id}/nodes/{id}/data` behaviour change under Sub 3.3:
- Default: wait for the synchronous outbox resolution (up to a
  per-call timeout, e.g. 5s). Response stays 200 with
  `file_lineage` populated, same as today.
- Timeout path: return 202 Accepted with
  `{pending_outbox_ids: [...]}` and the client polls
  `GET /runs/{id}/nodes/{id}/data` — which reflects the
  projector's eventual rewrite.

Phase 3 **does not** break the current 200-with-file-lineage
contract in the common case. The timeout path is the new
behaviour and is behind a client-visible response header
`X-Outbox-Status: resolved|pending`.

## 7. Sub-task 3.2 — Outbox infrastructure

### 7.1 Files

- `app/layer2_application/interfaces/outbox_repository_interface.py` (NEW)
  — `IOutboxRepository` with `list_unresolved(since_sequence=0)`,
  `record_attempt(outbox_id)`, `get_attempts(outbox_id)`.
- `app/layer4_frameworks/persistence/memory/memory_outbox_repository.py` (NEW).
- `app/layer4_frameworks/persistence/hana/hana_outbox_repository.py` (NEW).
  Piggybacks on `AIW_EVENT_LOG` for the main storage; only the
  attempt counter needs its own table (or a per-process memory
  dict with periodic flush).
- `app/layer4_frameworks/outbox/outbox_dispatcher.py` (NEW) —
  async task with the dispatch loop from §6.3.
- `app/layer4_frameworks/outbox/handlers/` (NEW directory) —
  one handler per `kind`:
  - `file_upload_handler.py`
  - `file_delete_handler.py`
  - `queue_enqueue_handler.py`
  - `stage_record_handler.py`
- `app/layer1_domain/events.py` (MODIFY) — add
  `OutboxRequested`, `OutboxResolved` dataclasses +
  `EventTopic.OUTBOX_REQUESTED` / `OUTBOX_RESOLVED`.
- `app/layer1_domain/services/projector/run_state_projector.py`
  (MODIFY) — two new reducer branches; `pending_outbox` field on
  `ProjectedRunState`. Bump `SCHEMA_VERSION` in `snapshot_codec.py`.
- `bootstrap.py` (MODIFY) — wire `OutboxDispatcher` into the
  FastAPI lifespan; start on startup, join on shutdown.

### 7.2 Dispatcher wiring

The dispatcher is a singleton per control-plane pod. Multi-pod
deployments run multiple dispatchers; duplicate attempts are
safe because each downstream call uses an idempotency key. If
duplicate-attempt volume becomes a problem, a per-dispatcher
lease can serialise (same pattern as the sweep task), but that's
YAGNI until we observe it.

### 7.3 Error-classification

- `RetryableError`: network timeouts, 5xx from the file service,
  Kafka enqueue failures. Retry with backoff.
- `PermanentError`: 4xx (especially 404/409/422), payload
  validation errors. Fail fast, surface to ops.

Error taxonomy lives in `outbox_errors.py`; each handler raises
one of the two base classes.

## 8. Sub-task 3.3 — DATA_EDIT migration

### 8.1 Call sites to migrate

`edit_node_data_use_case.py` (18 ops supported, each with a
download → mutate → upload cycle):

| Line | Call | Outbox kind |
|---|---|---|
| 767 | `_fs.head_file(new_file_id)` | Read-only — leave in-process; not a side effect |
| 791 | `_fs.query_file_data(new_file_id, params)` | Read-only — leave |
| 1086-90 | `_fs.download_file` + `_fs.upload_file` | **`file_upload`** (download stays inline) |
| 1139 | `_fs.query_file_data` | Read-only — leave |
| 1303 | `_fs.delete_file(fid)` | **`file_delete`** |
| 330 | `_dispatch_rerun(...)` | **`queue_enqueue`** (ResumeTaskAction dispatch) |

Pattern per write site:
```python
# Before
new_file_id = await self._fs.upload_file(mutated, filename, "text/csv")
target_artifact["file_id"] = new_file_id

# After
outbox_id = deterministic_outbox_id(task.id, op_index, "upload")
await self._event_log.append(OutboxRequested(
    outbox_id=outbox_id,
    kind="file_upload",
    payload={"filename": filename, "content_hash": sha256(mutated)},
    idempotency_key=sha256(mutated).hex(),
))
# Synchronous wait on OutboxResolved (§6.4 default model).
resolved = await self._outbox_awaiter.wait(outbox_id, timeout=5)
new_file_id = resolved.result["file_id"]
target_artifact["file_id"] = new_file_id
```

`deterministic_outbox_id` = `sha256(f"{task.id}:{op_index}:{kind}").hex()[:16]`.
Stable across replays.

### 8.2 The bytes-transport question

`OutboxRequested.payload` holds metadata only, not the CSV bytes
themselves — events are small JSON. Where do the bytes live?

Option A: upload in two phases. Handler uploads to a staging
area via an idempotency key **before** emitting the event; the
event carries the resulting `stg_file_id`; dispatcher then
moves/renames `stg_file_id` → final `file_id`. Requires file
service to support staging — it doesn't today.

Option B: the handler stashes bytes in a **local
payload-cache** keyed by `content_hash`, emits the event, and
the dispatcher reads from the cache. Replay-safe because
`content_hash` is deterministic and the cache is just an
optimisation — cache-miss path re-computes (but the handler
already computed the bytes once, so re-compute means re-reading
source file_id + re-applying the op).

Option C (**adopted**): keep the **current sync-upload behaviour
inside the outbox dispatcher's file_upload handler**. The
handler runs the mutation, takes the bytes as an argument via
an in-memory side-channel (`OutboxEnqueue` passes `(event,
payload_bytes)` as a tuple to the dispatcher's file_upload
handler which uploads with idempotency key). Replay
re-computes the bytes inside DATA_EDIT handler; idempotency key
prevents a second actual upload.

Option C is the pragmatic choice: no file-service changes, no
local cache, bytes flow through process memory like today. Only
the dedup surface moves into the outbox dispatcher.

### 8.3 Backward compatibility during rollout

Sub 3.3 lands with a feature flag `OUTBOX_DATA_EDIT={off,on}`
default `off`. When `off`, DATA_EDIT uses today's direct file-
service path. When `on`, uses the outbox. Observability: emit
counters `data_edit.outbox_used{mode=...}` per PATCH.

Soak gate to flip `on` in prod: 1 week of `on` in staging with
failure rate ≤ baseline + 0.1%.

## 9. Sub-task 3.4 — HITL resume migration

### 9.1 Call sites

`resume_pending_task_use_case.py:116-125` — `stage_recorder.record(PENDING→RUNNING)`.
`resume_run_use_case.py:80-85` — `stage_recorder.record(...)`.
`resume_run_use_case.py:90` — `queue.enqueue(ResumeRunAction)`.

All three become outbox entries. `stage_record` kind is trivial
— audit-log insert with `outbox_id` as the dedup key.

### 9.2 Queue-enqueue idempotency

`ResumeRunAction` handler today re-dispatches READY tasks. A
double-enqueue would re-enqueue the READY set twice. Today this
is guarded by the task state check ("already dispatched, skip")
on the worker side, but the margin is thin.

Outbox fix: `queue.enqueue(action)` becomes an outbox entry with
`idempotency_key = deterministic_outbox_id(run_id, "resume_run")`.
The dispatcher's `queue_enqueue` handler checks a per-run
in-memory "already enqueued this outbox_id" cache before
calling the real queue. Replay → same outbox_id → skip.

### 9.3 HTTP semantics

POST `/runs/{id}/resume` and POST `/runs/{id}/tasks/{id}/resume`:
remain synchronous. The `record()` call happens inline (short-
circuit: call stage recorder first, outbox only persists the
intent for audit replay). For resume, **correctness does not
require the stage record to succeed before the HTTP 200**, so
the outbox model is a correctness upgrade not a user-visible
change.

Feature flag: `OUTBOX_HITL_RESUME={off,on}` default `off`.

## 10. Sub-task 3.5 — ExecutorConsumer

### 10.1 Template

The investigation found that today's `DispatcherConsumer`
(`app/layer4_frameworks/dispatcher/dispatcher_consumer.py`) is
**publisher-tee-based, not broker-subscribed** — it hooks into
`DispatcherTeeingPublisher` which calls `on_node_output_produced`
synchronously in the publishing thread.

The ExecutorConsumer cannot use the tee pattern — if the
Executor is in a separate pod it has no publisher to hook into.
It must be a **real broker consumer** subscribed to the
`node.input` topic.

That makes the Executor the **first** production broker
consumer in this codebase (Socket.IO consumer aside). The
patterns it establishes matter:
- Offset commit after callback success only (§5.1).
- Deterministic handler-result dedup by `(run_id, node_id, attempt)`
  stored in a local `executed_keys` set (or Redis in prod).
- Back-pressure: bounded asyncio.Queue between Kafka poll loop
  and handler invocation; defer commit until handler completes.

### 10.2 Structure

```
ExecutorConsumer
├── start()                         # subscribe to "node.input", kick off run_forever()
├── _on_message(msg)                # deserialize NodeInput, enqueue to handler loop
├── run_forever()                   # async worker: pop → invoke → emit → commit
├── _invoke_handler(node_input)     # load node + run + workflow, get handler, call handle()
├── _handle_result(result, ctx)     # emit NodeOutputProduced / TaskCompleted / TaskFailed
├── _dedup_check(run_id, node_id, attempt)  # skip if already executed
└── stop()                          # graceful shutdown: drain in-flight then close consumer
```

Files:
- `app/layer4_frameworks/executor/executor_consumer.py` (NEW).
- `app/layer4_frameworks/executor/executor_dedup.py` (NEW) —
  in-process set of `(run_id, node_id, attempt)` with LRU
  eviction (keys are short; 10k entries ≈ 1 MB).
- `app/layer4_frameworks/executor/executor_parity_comparator.py`
  (NEW) — shadow-mode comparator comparing engine emissions vs.
  Executor emissions.
- `main.py` (MODIFY) — start `ExecutorConsumer` in lifespan
  under `EXECUTOR_MODE ∈ {shadow, executor}`.
- `app/layer4_frameworks/config/app_config.py` (MODIFY) —
  `EXECUTOR_MODE={in_process,shadow,executor}`.

### 10.3 Shadow mode

Under `EXECUTOR_MODE=shadow`:
- Engine continues to invoke handlers in-process (authoritative
  emissions).
- ExecutorConsumer also runs; receives `node.input` via the
  broker; invokes the handler.
- **Crucially:** ExecutorConsumer does NOT emit authoritative
  events in shadow mode. Instead, it records its computed
  result to a parity recorder.
- Comparator diffs engine-emitted events vs. Executor-computed
  results by `(run_id, node_id, attempt, event_type)`.

Parity gate: 7 days staging + 14 days prod with drift=0 before
flipping to `executor`.

### 10.4 Authoritative mode

Under `EXECUTOR_MODE=executor`:
- Engine's `dispatch_or_handle_task` no longer invokes handlers
  directly. It emits `NodeInput` and returns.
- ExecutorConsumer is the only handler invoker.
- Engine retains task creation + enqueue (emits `TaskDispatched`
  only — not `TaskCompleted`).

### 10.5 Engine changes

Phase 3 extracts handler invocation, so engine code shrinks:
- Delete the `invoke_handler` call at
  `orchestration_engine.py:1651-1657`.
- Replace with emit-only path: emit `NodeInput` + return.
- `_handle_result` branching (lines 1678+) moves to the
  ExecutorConsumer.
- The orchestration queue's `ExecuteNodeAction` path (worker.py)
  becomes: the worker emits `NodeInput` and is done. The real
  handler work happens in the Executor.

## 11. Sub-task 3.6 — Kill-a-replica chaos test

### 11.1 What it tests

`tests/integration/test_executor_kill_recovery.py` (NEW):

1. Start two Executor processes on the same `node.input` Kafka
   topic (consumer group shared).
2. Emit a `NodeInput` for a DATA_EDIT node. Force routing to
   Executor A.
3. Mid-handler (instrumented to block on a controllable event
   before the file upload), SIGKILL Executor A.
4. Kafka consumer-group session times out, rebalances; Executor
   B receives the redelivered `NodeInput`.
5. Assertions:
   - Exactly one `OutboxResolved(kind=file_upload)` event in
     the log.
   - `file_lineage` points to a single real `file_id`.
   - No orphan uploads in the file service (via
     `file_service.list_files_by_idempotency_key`).
   - The HTTP PATCH caller sees a successful 200 (possibly
     delayed by the rebalance timeout).

### 11.2 Infrastructure

The test uses `multiprocessing.Process` for the two Executors
and a real Kafka broker (testcontainers-python or the existing
`confluent-kafka` mock harness). The file service is the
in-memory mock with idempotency-key support added for this test.

Keep it a `@pytest.mark.chaos` test — expensive, not run in
default CI; gated behind a `CHAOS_TESTS=1` env var so the soak
runs pick it up but local dev doesn't pay the cost.

### 11.3 What it doesn't test

- Handler-level crashes (e.g. Python `RuntimeError` inside the
  handler). Those are covered by the existing dispatcher-parity
  tests + new executor-parity tests.
- Broker unavailable. Separate test.
- Slow-handler timeouts. Separate test.

## 12. Sub-task 3.7 — Standalone deployment

### 12.1 Container image

New Dockerfile `deployment/docker/Dockerfile.executor`:
- Base from the existing workflow-control-plane base image
  (reuses Python deps, SDK).
- Entry point: `python -m app.executor_main` — a thin
  `asyncio.run(main())` that wires the infra factory +
  `ExecutorConsumer` only (no FastAPI, no HTTP routes).
- Ports: no HTTP. Metrics on :8090 (prometheus pull).
- Env: `INFRA_MODE`, `KAFKA_BOOTSTRAP_SERVERS`,
  `EVENT_LOG_BACKEND`, `HANA_*` (shared with control plane).

### 12.2 Kubernetes / CF manifest

- Kubernetes: `deployment/k8s/executor-deployment.yaml` — 2
  replicas default, HPA on CPU + `node.input` topic lag.
- Cloud Foundry: `deployment/cf/manifest-executor.yml` —
  separate app name `wio-executor`, binds same services
  (HANA, Event Mesh).
- Termination grace period ≥ 60 s to drain in-flight handlers
  (same as M.3's pattern).
- `INSTANCE_ID` pinned from pod name (stateful-like) so
  response-topic subscriptions survive restarts — reuses the
  M.3 `INSTANCE_ID` plumbing.

### 12.3 Observability

New dashboards:
- Handler invocation rate by `node_type`.
- p50/p95/p99 handler duration.
- `node.input` topic lag per Executor pod.
- Outbox dispatcher: pending count, failure rate, per-kind
  latency.
- Kill-a-replica recovery time (from SIGKILL to redelivery
  completion).

Alerts:
- Outbox pending count > 100 for > 5 min.
- `OutboxResolved(outcome="failed")` rate > 1/min.
- Any handler p99 > 30 s.

## 13. Rollout

Five gates, each with its own PR chain.

### 13.1 Gate 0 — prerequisites

Merge Sub 3.0 (Kafka ack fix) + Sub 3.1 (design doc) + Sub 3.2
(outbox infra). Feature flags at `off`. Verify: test suite green;
bootstrap stays backward-compatible.

### 13.2 Gate 1 — outbox on for DATA_EDIT + HITL

Merge Sub 3.3 + Sub 3.4 with `OUTBOX_*=off` default. Flip to
`on` in staging; soak 1 week; verify outbox dispatcher
metrics are healthy.

Flip `on` in prod after staging passes. This is independent of
the Executor — the outbox pattern is useful on its own for
correct redelivery semantics even before execution moves out of
process.

### 13.3 Gate 2 — ExecutorConsumer in shadow

Merge Sub 3.5 with `EXECUTOR_MODE=shadow`. Two paths run
concurrently; parity comparator catches drift. Soak 7 days
staging + 14 days prod with drift=0.

### 13.4 Gate 3 — ExecutorConsumer authoritative

Flip `EXECUTOR_MODE=executor` in staging. Engine no longer
invokes handlers; all handler work flows through the Executor.
Verify handler invocation rate on the Executor pod matches the
engine's previous rate. Flip prod after 1 week staging clean.

### 13.5 Gate 4 — standalone deployment

Merge Sub 3.6 + Sub 3.7. Deploy the Executor as its own
container in staging. Verify the Executor pod can be
started/stopped freely; handler invocation continues through
the kill-a-replica chaos test.

Flip prod: add Executor pods; downscale control-plane pod's
Executor-thread count to 0 (i.e. disable the in-process
consumer). Observe handler invocation has moved entirely to
the new pod group.

## 14. Hazards + mitigations

### 14.1 Outbox bytes-transport under replay (§8.2)

The adopted Option C: DATA_EDIT re-computes mutated bytes on
replay. This is O(CSV-size) per replay attempt. Typical
DATA_EDIT payloads are small (< 1 MB); replay frequency is
≤ 1-per-rebalance.

Mitigation: cap retry attempts at MAX_ATTEMPTS=5; emit a
metric for replay cost; fall back to Option B (local payload
cache) in a follow-up if cost becomes visible.

### 14.2 Executor consuming ahead of engine

In authoritative mode the engine emits `NodeInput` and the
Executor consumes asynchronously. If the Executor is slow
(long handler, handler queue backed up), runs stall. Mitigation:
handler-specific HPA; `node.input` lag alert; per-handler
timeout (kill + requeue the `NodeInput`).

### 14.3 Parity-comparator noise from ordering

Pure-output handlers are deterministic, so `NodeOutputProduced`
payloads match exactly between engine and Executor. But the
**order of emissions** may differ across runs (engine emits
sequentially in-loop; Executor emits from a separate process
potentially interleaved with other runs). Comparator must
compare by `(run_id, node_id, attempt, event_id)` identity,
**not** by sequence-in-log. Already Phase 2b's approach; reuse
the same pattern.

### 14.4 Outbox dispatcher backlog at startup

On Executor pod restart with a pending-outbox backlog: dispatcher
has to drain before new entries flow smoothly. Cold-start SLA:
< 30 s to process 1000 pending entries. Sizing: run the dispatcher
with bounded concurrency (e.g. 10 concurrent side-effect calls).

### 14.5 `SCHEMA_VERSION` bump invalidates Phase 4 snapshots

Sub 3.2 bumps `snapshot_codec.SCHEMA_VERSION` from 1 to 2
(adding `pending_outbox`). Every run's prior snapshot falls
back to full replay on the next cold start; self-heals on the
next threshold crossing (Phase 4 retention). Expected one-time
cost at the Sub-3.2 deploy.

### 14.6 Handler process memory pressure

Executor process hosts all 13 handlers. A LOOP handler expanding
10k iterations in-memory is fine today because the engine is
single-process. In a separate Executor, the same handler still
runs in-process — no change. But if multiple runs concurrently
execute heavy handlers, per-pod memory pressure is new.
Mitigation: per-pod memory limit + OOM restart; the outbox
makes replay safe, so an OOM-kill just redelivers.

## 15. Test matrix

| Test file | Sub | Purpose | Count |
|---|---|---|---|
| `test_outbox_events.py` | 3.1 | Event shape round-trip; deterministic outbox_id derivation | 6 |
| `test_outbox_repository.py` | 3.2 | `list_unresolved` / `record_attempt` / `get_attempts` against memory + HANA stubs | 12 |
| `test_outbox_dispatcher.py` | 3.2 | Dispatcher loop: retry+backoff, DLQ on max attempts, idempotency-key dedup | 15 |
| `test_data_edit_outbox.py` | 3.3 | Every DATA_EDIT op, with `OUTBOX_DATA_EDIT=on`; assert exactly-one upload under synthetic replay | 20 |
| `test_hitl_resume_outbox.py` | 3.4 | resume_pending_task + resume_run under replay; assert single stage_record + single enqueue | 10 |
| `test_executor_consumer.py` | 3.5 | Consumer subscribes, invokes handler, emits output events; dedup; graceful shutdown | 18 |
| `test_executor_shadow_parity.py` | 3.5 | Engine + Executor emit identical events for all 13 handler types | 26 (2 per handler) |
| `test_executor_kill_recovery.py` | 3.6 | Chaos test (marked, not in default CI) | 1 |
| `test_outbox_integration.py` | 3.2-3.4 | End-to-end PATCH → outbox → resolved → rerun | 5 |

**Total new tests:** ~113. Non-regression: existing handler tests
(`test_condition_handler.py`, `test_switch_handler.py`, etc.) all
keep passing unchanged — the Executor reuses the same handler
implementations.

## 16. What Phase 3 does NOT do

- **Delete the orchestration queue.** `EditNodeDataAction`
  routing via the queue stays; Phase 3 only changes what
  happens **inside** the handler (outbox for side effects).
- **Delete the engine's task creation.** Engine still creates
  `Task` rows, still emits `TaskDispatched`. Executor picks up
  from `NodeInput`.
- **Reshape the dispatcher.** Dispatcher continues emitting
  `node.input` as designed in Phase 2b; Phase 3 adds the
  consumer.
- **Change the worker-executor service.** TASK nodes still
  round-trip through the existing worker-executor. Phase 3's
  Executor calls worker-executor's HTTP surface identically.
- **Introduce new event types beyond outbox.** No new
  lifecycle events; existing `NodeInput`, `NodeOutputProduced`,
  `TaskCompleted`, `TaskFailed` cover the path.

## 17. What remains after Phase 3

Phase 3 is **structurally complete** — handler invocation, side
effects, and the standalone deployment all exist as event-driven
paths gated behind feature flags. But "true end-to-end
event-driven" requires closing four gaps captured in the
post-Gate-3 verification doc
([`phase-3-verification.md`](./phase-3-verification.md)).

### 17.1 Gaps that block default `EXECUTOR_MODE=executor`

These three sites are inline today and remain inline regardless of
flag combinations. Under `EXECUTOR_MODE=executor` they cause the
control flow to break at runtime — the engine emits `NodeInput` and
returns, but no other code path picks up the slack:

- **Task creation for downstream nodes** (Gap 1 in the verification
  doc). Engine inline code creates `Task` entities in
  `_exec_uniform`'s post-handler-result block. Under executor mode
  this is bypassed → `DispatcherConsumer` emits `NodeInput` with
  `task_id=""` for the next node → executor invokes with `task=None`
  → no Task entity in the repo → projector's task view is missing it.
  Fix: a `TaskCreatorConsumer` (or have `DispatcherConsumer` create
  tasks during fan-out). ~200 LoC.
- **Run start** (Gap 2). `orchestrate_start` walks entry nodes
  synchronously from the `POST /runs` handler. Fix: a
  `RunStartConsumer` subscribed to `RunStarted` that creates entry
  tasks. ~150 LoC.
- **Child workflow start** (Gap 3). `_start_child_workflow` runs
  inline in the WORKFLOW handler's post-result code; under executor
  mode it is bypassed entirely. Fix: emit `ChildRunRequested` from
  the executor; reuse the `RunStartConsumer`. ~80 LoC.

### 17.2 Other items deferred from Phase 3 proper

- **Engine-side parity hook insertion.** The six wrapped sites in
  `orchestration_engine.py` do not call
  `consumer.record_engine_invocation(...)`, so the parity comparator
  in `shadow` mode sees only the executor side. Mechanical 6-line
  patch; defer until the gaps above close so soak measurements
  cover the real default-mode path.
- **Legacy `_run_loop_iterations` retirement.** Queued mode
  (`EVENT_DRIVEN_LOOP=true`, default) is event-driven; legacy
  imperative path lingers for one or two tests. Delete after test
  migration. ~100 LoC.
- **BUILTIN/TASK Executor pool split** — master doc §4.3 step 2.
  Observe which handler family dominates CPU; split if one does.
- **Orchestration-engine 18 inner saves retry wrap** — still
  deferred; requires prod CAS-409 observability data.
- **Async PATCH response model** — §6.6's timeout path (202 +
  polling) is defined but not exercised by default. If PATCH
  latency becomes a problem, flip on.
- **Projector extraction** — today's in-process projector could
  become its own service (mirror pattern of Executor). Low
  priority; projector is cheap.

### 17.3 Net summary

The system is **event-shaped at every routing boundary AND every
side-effect boundary**, with handler invocation behind a flag.
Routing (Dispatcher) and read state (Projector) are authoritative
event-driven today. Side-effecting handlers route through the
outbox when the per-feature flags are on. Handler invocation moves
to the standalone Executor when `EXECUTOR_MODE=executor` is on,
**but** task creation + run start + child-workflow start remain
inline — meaning a default flip to `executor` mode requires the
follow-up Sub 4.x work above.

For the in-flight default mode (`EXECUTOR_MODE=in_process`) the
system continues to function exactly as pre-Phase-3 with the
addition of optional outbox routing and shadow-mode parity
infrastructure.

## 18. Open questions

1. **OutboxDispatcher singleton vs. one-per-pod.** Multiple
   control-plane pods means multiple dispatchers; idempotency
   keys make it safe but wastes RPC. Per-pod lease (k8s
   lease API / HANA advisory lock) would serialise. Decide
   during Sub 3.2 based on replay-volume estimate.
2. **Where does the executor's `executed_keys` dedup table live
   in prod?** In-process LRU works for single-pod; multi-pod
   needs either Redis or per-run partition-sticky routing
   (M.1 already delivers the latter). Adopt the partition-
   sticky path by default; Redis only if needed.
3. **Do `file_service` idempotency keys support
   cross-client sharing?** Phase 3 assumes yes — the hash
   identifies the file regardless of caller. Verify with the
   file-service team before Sub 3.3.
4. **Async PATCH semantics under timeout path.** If adopted,
   does the frontend polling loop use Socket.IO or a REST
   poll? Frontend decision; not blocking Phase 3.

## 19. Reference points in current code

- Handler registry + invocation: `app/layer1_domain/services/orchestration_engine.py:1591-1770`
- `DispatcherConsumer` (template, publisher-tee not broker): `app/layer4_frameworks/dispatcher/dispatcher_consumer.py:62-115`
- `DispatcherTeeingPublisher`: `bootstrap.py:466-509`
- `NodeInput` event: `app/layer1_domain/events.py:54` (topic `node.input`), emitted `orchestration_engine.py:1630-1644`
- Kafka client ack semantics: `app/layer4_frameworks/messaging/kafka_broker_client.py:162-232` — **currently at-most-once; fix in 3.0**
- Event Mesh client: `app/layer4_frameworks/messaging/event_mesh_broker_client.py`
- DATA_EDIT file-service calls: `edit_node_data_use_case.py:767, 791, 1086-90, 1139, 1303`
- DATA_EDIT rerun dispatch: `edit_node_data_use_case.py:330-332`
- HITL resume side effects: `resume_pending_task_use_case.py:116-125`, `resume_run_use_case.py:80-90`
- Deterministic event ids: `app/layer1_domain/services/event_id_helper.py`
- Snapshot SCHEMA_VERSION (bump point): `app/layer1_domain/services/projector/snapshot_codec.py:27`
- Event log schema: `db/migrations/025_create_aiw_event_log.sql`
- Next migration number: **029** (after Phase 4's 028)
