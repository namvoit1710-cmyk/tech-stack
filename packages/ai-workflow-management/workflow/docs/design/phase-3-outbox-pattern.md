# Phase 3 Outbox Pattern

> Self-contained design for the transactional outbox used by side-effect
> handlers (DATA_EDIT file operations, HITL resume). Paired with
> `phase-3-executor-extraction.md` §6-9; read that first for motivation.

## 1. Scope

The outbox layer sits between:

- **Producer side:** side-effect handlers — `edit_node_data_use_case.py`
  (DATA_EDIT PATCH path) and `resume_pending_task_use_case.py` /
  `resume_run_use_case.py` (HITL resume path) — that today call the
  file-service / stage-recorder / orchestration-queue directly.
- **Consumer side:** a new `OutboxDispatcher` background task that reads
  unresolved entries from the event log and performs the real
  side effect with an idempotency key.

Every side effect becomes a pair of log events:

```
OutboxRequested(outbox_id, kind, payload, idempotency_key)
        │
        ▼ (dispatcher performs the real call, dedups with idempotency_key)
OutboxResolved(outbox_id, outcome, result, error?)
```

Both events live in `AIW_EVENT_LOG` alongside `NodeOutputProduced`,
`TaskCompleted`, etc. No new persistence table. Dedupe for replay uses
the existing `(run_id, event_id)` uniqueness (Phase 4b.1 invariant).

This doc is non-executable: its output is code written by Subs 3.2-3.4.

## 2. Event shapes

Added to `app/layer1_domain/events.py`.

```python
@dataclass(frozen=True)
class OutboxRequested(DomainEvent):
    """Side-effect intent recorded by a handler.

    Carries the minimal metadata the dispatcher needs to perform the real
    call idempotently. Payload bytes (e.g. CSV for file_upload) are NOT
    stored here — see §7 for how they reach the dispatcher.
    """
    outbox_id: str            # deterministic; see §4
    kind: str                 # "file_upload" | "file_delete" | "queue_enqueue" | "stage_record"
    payload: dict             # kind-specific metadata; see §3
    idempotency_key: str      # passed to downstream to dedupe retries


@dataclass(frozen=True)
class OutboxResolved(DomainEvent):
    """Dispatcher-written terminal result of an ``OutboxRequested``."""
    outbox_id: str
    outcome: str              # "ok" | "failed"
    result: dict              # kind-specific; e.g. {"file_id": "f_abc"}
    error: str | None = None  # populated iff outcome=="failed"
```

Topic constants in the same module:

```python
class EventTopic:
    ...
    OUTBOX_REQUESTED = "outbox.requested"
    OUTBOX_RESOLVED = "outbox.resolved"
```

These are event-log records, not Kafka topics consumed by a broker. The
existing `AIWEventLogAppender` (Phase 1a) writes both record kinds as
ordinary log rows.

## 3. Kinds and payloads

Four `kind` values cover the Phase 3 side-effect surface. Adding a new
kind means adding a row here + a handler module under
`layer4_frameworks/outbox/handlers/`.

### 3.1 `file_upload`

Triggered from `edit_node_data_use_case.py:1090` (the sole
`self._fs.upload_file(...)` call site).

| Field | Value |
|---|---|
| `payload.filename` | From the DATA_EDIT op (e.g. `"customers_edited.csv"`) |
| `payload.content_hash` | `sha256(mutated_bytes).hex()` — computed before the event is appended |
| `payload.content_type` | `"text/csv"` |
| `idempotency_key` | Same `content_hash` — identical bytes produce identical keys |
| `OutboxResolved.result` | `{"file_id": <uploaded id>}` |

**Bytes transport:** the handler runs the mutation, computes
`mutated_bytes` and `content_hash`, appends `OutboxRequested`, and keeps
`mutated_bytes` in process memory for the in-flight dispatcher call.
Replay is safe because DATA_EDIT re-computes the same bytes from the
source file_id — the file-service's `idempotency_key` dedupe covers the
second upload. See §7 (Option C) for the full argument.

### 3.2 `file_delete`

Triggered from `edit_node_data_use_case.py:1303` (orphan-cleanup
branch of the rerun path).

| Field | Value |
|---|---|
| `payload.file_id` | `str` — file to delete |
| `idempotency_key` | `file_id` — delete is naturally idempotent |
| `OutboxResolved.result` | `{}` — no return value |

### 3.3 `queue_enqueue`

Triggered from:
- `resume_run_use_case.py:90` (`ResumeRunAction` enqueue)
- `edit_node_data_use_case.py:330` (rerun dispatch via `_dispatch_rerun`;
  the inner enqueue call — see §6.2 for why this is wrapped too)

| Field | Value |
|---|---|
| `payload.action_type` | `"ResumeRunAction"` \| `"ResumeTaskAction"` etc. |
| `payload.action_payload` | The action's serialized dict |
| `idempotency_key` | `outbox_id` |
| `OutboxResolved.result` | `{}` — enqueue is fire-and-forget |

The dispatcher's `queue_enqueue` handler keeps a per-pod LRU of
already-enqueued `outbox_id`s as a local short-circuit. Replay hitting
an id in the LRU skips the real enqueue. Cold start after restart
re-uses the event-log check (projector branch §8) instead.

### 3.4 `stage_record`

Triggered from three stage-recorder call sites:
- `resume_pending_task_use_case.py:120, 177, 429`
- `resume_run_use_case.py:80`

| Field | Value |
|---|---|
| `payload.stage_kind` | e.g. `"PENDING_TO_RUNNING"` |
| `payload.stage_payload` | Attribute dict for the audit row |
| `idempotency_key` | `outbox_id` |
| `OutboxResolved.result` | `{"audit_row_id": <...>}` when available |

## 4. Deterministic `outbox_id`

The id must be stable across replays so the same `OutboxRequested` +
`OutboxResolved` pair always co-reference. Derivation lives in
`app/layer1_domain/services/event_id_helper.py` alongside the existing
`deterministic_event_id()`:

```python
def deterministic_outbox_id(run_id: str, task_id: str, op_index: int, kind: str) -> str:
    """Stable id for an outbox entry — survives handler replay."""
    return sha256(f"{run_id}:{task_id}:{op_index}:{kind}".encode("utf-8")).hexdigest()[:16]
```

For HITL resume (which has no `task_id` on the resume_run path), fall
back to `run_id + "resume_run"` as the stable seed; for
`resume_pending_task`, `task_id + "resume_pending"`.

Idempotency keys differ from `outbox_id` for `file_upload` (content
hash) because two different handler invocations can produce the same
bytes and the file-service should dedupe them to one blob.

## 5. Dispatcher semantics

`OutboxDispatcher` (`app/layer4_frameworks/outbox/outbox_dispatcher.py`)
is an async loop started in `bootstrap.py` lifespan.

```
loop forever:
    entries = view_store.list_unresolved_outbox_requests()
    for entry in entries:
        try:
            result = await _handler_for(entry.kind).perform(entry)
            await event_log.append(OutboxResolved(entry.outbox_id, "ok", result))
        except RetryableError as e:
            if attempts[entry.outbox_id] >= MAX_ATTEMPTS:
                await event_log.append(OutboxResolved(entry.outbox_id, "failed", {}, str(e)))
            else:
                attempts[entry.outbox_id] += 1
                await asyncio.sleep(backoff(attempts[entry.outbox_id]))
        except PermanentError as e:
            await event_log.append(OutboxResolved(entry.outbox_id, "failed", {}, str(e)))
    await asyncio.sleep(POLL_INTERVAL_S)
```

- **`list_unresolved_outbox_requests`:** served by the projector's new
  reducer (§8). No direct log query.
- **`_handler_for(kind)`:** dispatch table populated from
  `layer4_frameworks/outbox/handlers/{kind}.py`. Each handler exposes
  `async def perform(entry) -> dict` and raises
  `RetryableError` or `PermanentError` (base classes in
  `outbox_errors.py`).
- **Retry budget:** `MAX_ATTEMPTS = 5`, exponential backoff with 20%
  jitter. Attempts counter is in-memory per pod (`dict[outbox_id, int]`),
  flushed on `OutboxResolved` emission. No persistence — a pod crash
  resets the counter, which is fine because the next pod inherits the
  unresolved entry and gets its own 5 attempts.
- **Poll interval:** 100 ms when entries exist; 1 s idle.

### 5.1 Singleton vs per-pod

Deferred to Sub 3.2 implementation. Initial position: **run one
dispatcher per pod, accept duplicate attempts** — idempotency keys
protect the downstream. If observed RPC volume is high enough to
matter, add a HANA advisory-lock lease (same pattern as the sweep
task) in a follow-up.

### 5.2 DLQ

No separate DLQ topic in Phase 3. Failed entries are visible as
`OutboxResolved(outcome="failed")` rows in the event log. Ops
dashboard filters on the projector's `pending_outbox` view
(failed entries stay visible until manually resolved; see §8).

A dedicated metric `outbox.resolved.failed{kind=...}` alerts ops.
Alert threshold: > 1/minute sustained for > 5 min.

## 6. Producer side — handler migration shape

Each side-effect site changes from direct call to outbox + wait.
`OutboxAwaiter` is a small helper (`layer2_application/services/outbox_awaiter.py`)
that uses the existing `ResponseRegistry` pattern from Phase 3c:
register a correlation id → await a future → projector tee resolves
the future on `OutboxResolved` fold.

### 6.1 DATA_EDIT `upload_file` site (line 1090)

```python
# Before
new_file_id = await self._fs.upload_file(mutated, filename, "text/csv")
target_artifact["file_id"] = new_file_id

# After
outbox_id = deterministic_outbox_id(run.id, task.id, op_index, "file_upload")
content_hash = sha256(mutated).hexdigest()
await self._event_log.append(OutboxRequested(
    outbox_id=outbox_id,
    kind="file_upload",
    payload={"filename": filename, "content_hash": content_hash, "content_type": "text/csv"},
    idempotency_key=content_hash,
))
# Register bytes side-channel for the in-flight dispatcher call (§7).
self._outbox_bytes_channel.put(outbox_id, mutated)
resolved = await self._outbox_awaiter.wait(outbox_id, timeout=self._outbox_wait_timeout_s)
new_file_id = resolved.result["file_id"]
target_artifact["file_id"] = new_file_id
```

### 6.2 DATA_EDIT rerun dispatch (line 330)

`_dispatch_rerun` internally calls `self._queue.enqueue(...)`. Wrap the
enqueue call as `queue_enqueue` outbox entry — the rest of
`_dispatch_rerun` stays in-process.

### 6.3 HITL resume sites

Identical pattern — append `OutboxRequested(kind="stage_record", ...)`
before returning from the use case, `await outbox_awaiter.wait(...)`
for 200-with-body semantics.

`resume_run_use_case.py:90` enqueue becomes a `queue_enqueue` outbox
entry. The use case returns 200 when the entry is resolved; if the
client sets `X-Async-Resume: true`, return 202 with the `outbox_id`
and skip the wait.

## 7. Bytes transport under replay (Option C, adopted)

`OutboxRequested.payload` holds metadata only. For `file_upload` the
CSV bytes reach the dispatcher through a per-pod in-memory channel
(`OutboxBytesChannel`) keyed by `outbox_id`:

```python
class OutboxBytesChannel:
    def put(self, outbox_id: str, payload: bytes) -> None: ...
    def pop(self, outbox_id: str) -> bytes | None: ...
```

**Happy path:** handler appends the event, puts bytes in channel,
awaits resolution. Dispatcher pops bytes, uploads with the content
hash idempotency key, appends `OutboxResolved`.

**Replay path (different pod, pod restart, consumer rebalance):** the
channel is empty because it's in-memory. Dispatcher calls the
file-service's `upload_file(mutated_bytes=???, idempotency_key=...)`
endpoint — but has no bytes. Resolution: **DATA_EDIT handler re-runs
on replay** (Kafka `node.input` redelivery → ExecutorConsumer → same
handler invocation), re-computing the same mutated bytes from the
source file_id and re-appending `OutboxRequested`. The event log
dedupes on `(run_id, event_id)`, so the event append is a no-op —
but the bytes are back in the channel. Dispatcher finds the entry
unresolved, calls `upload_file` with the SAME `idempotency_key`
(the content hash), and the file-service returns the existing
`file_id` instead of creating a new blob.

Cost: replay does O(CSV-size) of mutation work. Typical DATA_EDIT
payloads are < 1 MB; Kafka rebalance is rare. Acceptable.

If replay cost becomes visible (metrics: `outbox.replay.rate`,
`outbox.replay.latency_ms`), fall back to Option B (content-hash
keyed local disk cache) in a follow-up.

## 8. Projector reducer branch

`ProjectedRunState` (`app/layer1_domain/services/projector/projected_run_state.py`)
gains one field:

```python
pending_outbox: dict[str, dict] = field(default_factory=dict)
# key: outbox_id
# value: {"kind": str, "requested_at": iso8601, "attempts": int}
```

`run_state_projector.py::apply_event` gains two branches:

```python
elif event_type == "OutboxRequested":
    pending = dict(state.pending_outbox)
    pending[payload["outbox_id"]] = {
        "kind": payload["kind"],
        "requested_at": envelope.timestamp.isoformat(),
        "attempts": 0,
    }
    return replace(state, pending_outbox=pending)

elif event_type == "OutboxResolved":
    pending = dict(state.pending_outbox)
    entry = pending.pop(payload["outbox_id"], None)
    # Keep failed entries visible for ops — move to a separate dict.
    if payload["outcome"] == "failed" and entry is not None:
        failed = dict(state.failed_outbox)
        failed[payload["outbox_id"]] = {**entry, "error": payload.get("error")}
        return replace(state, pending_outbox=pending, failed_outbox=failed)
    return replace(state, pending_outbox=pending)
```

`failed_outbox: dict[str, dict]` is also new. Failed entries require
an operator-initiated resolution (a future `outbox.retry` admin
action); until then they remain in `failed_outbox` for dashboard
visibility.

### 8.1 View-store contract

`IViewStore.list_unresolved_outbox_requests(limit: int)` returns the
first `limit` entries from any run's `pending_outbox`. Used only by
`OutboxDispatcher`. Implementation is a simple scan over the
projector's in-memory state.

### 8.2 `SCHEMA_VERSION` bump

`snapshot_codec.py::SCHEMA_VERSION` goes from `1` to `2`. Existing
snapshots become invalid on load and the reader falls back to full
replay (Phase 4's existing path). One-time cost at the Sub 3.2 deploy.
Snapshots rebuild naturally on the next threshold crossing.

## 9. Consumer side — HTTP semantics

### 9.1 PATCH `/runs/{id}/nodes/{id}/data`

**Default:** the use case awaits `OutboxResolved` via `OutboxAwaiter`
with a 5 s per-call timeout (configurable via
`OUTBOX_HTTP_WAIT_TIMEOUT_S`). Response stays 200 with
`file_lineage` populated — same contract as today.

**Timeout path:** after 5 s of waiting, return 202 Accepted:
```json
{"status": "pending", "pending_outbox_ids": ["abc123"], "poll_url": "/runs/.../nodes/.../data"}
```
plus the header `X-Outbox-Status: pending`. The client polls
`GET /runs/{id}/nodes/{id}/data`, which reads from the projector's
eventual rewrite.

### 9.2 POST `/runs/{id}/resume` and `/runs/{id}/tasks/{id}/resume`

Same default-sync semantics. The 202 timeout path is identical
structurally. Clients can opt into async via
`X-Async-Resume: true` request header, in which case the endpoint
returns 202 immediately without waiting — useful for long-running
`stage_recorder.record` under HANA pressure.

### 9.3 Header summary

| Header | Direction | Purpose |
|---|---|---|
| `X-Outbox-Status: resolved\|pending` | response | did the sync wait complete in-time |
| `X-Async-Resume: true` | request | skip the sync wait, return 202 immediately |
| `X-Outbox-Id: <id>` | response | correlation id for pending entries |

## 10. Feature flags

| Flag | Default | Scope |
|---|---|---|
| `OUTBOX_DATA_EDIT` | `off` | gate DATA_EDIT migration (Sub 3.3) |
| `OUTBOX_HITL_RESUME` | `off` | gate HITL resume migration (Sub 3.4) |
| `OUTBOX_HTTP_WAIT_TIMEOUT_S` | `5.0` | HTTP sync-wait budget |
| `OUTBOX_DISPATCHER_POLL_INTERVAL_S` | `1.0` | dispatcher idle poll |
| `OUTBOX_MAX_ATTEMPTS` | `5` | retry budget per entry |

All flipped `on` in staging for a 1-week soak before prod.

## 11. Test surface

| Sub | New test file | Count |
|---|---|---|
| 3.2 | `test_outbox_events.py` | 6 |
| 3.2 | `test_outbox_repository.py` | 12 |
| 3.2 | `test_outbox_dispatcher.py` | 15 |
| 3.2 | `test_outbox_bytes_channel.py` | 4 |
| 3.2 | `test_outbox_projector_branch.py` | 8 |
| 3.3 | `test_data_edit_outbox.py` | 20 |
| 3.4 | `test_hitl_resume_outbox.py` | 10 |
| 3.2-3.4 | `test_outbox_integration.py` | 5 |

Existing handler tests (`test_edit_node_data_use_case.py`, etc.) all
keep passing under `OUTBOX_*=off`. Tests that verify the new path
run under parameterization with `OUTBOX_*=on`.

## 12. Open questions (deferred to implementation)

1. **`file_service.upload_file` idempotency-key cross-client sharing.**
   Phase 3 assumes the `idempotency_key` parameter identifies the
   file globally (any caller with the same content hash gets the
   same `file_id`). Verify with the file-service team before Sub 3.3.
   If false, fall back to a per-call idempotency key (`outbox_id`)
   and live with one redundant upload per replay.
2. **Failed-entry admin tooling.** `failed_outbox` is visible via
   the projector but has no retry endpoint. Out of scope for Phase 3;
   tracked as a follow-up for the ops runbook.
3. **Snapshot compatibility under the `SCHEMA_VERSION` bump.**
   Self-healing replay works, but high-cardinality snapshot stores
   (prod) may see an initial latency spike. Pre-warm by triggering
   a targeted rebuild job during the 3.2 deploy window.

## 13. Reference anchors

- DATA_EDIT file-service calls: `edit_node_data_use_case.py:767, 791, 1086, 1090, 1139, 1303`
- DATA_EDIT rerun dispatch: `edit_node_data_use_case.py:330, 877, 1007`
- HITL stage records: `resume_pending_task_use_case.py:120, 177, 429`,
  `resume_run_use_case.py:80`
- HITL queue enqueue: `resume_run_use_case.py:90`,
  `resume_pending_task_use_case.py:440`
- Deterministic event ids: `app/layer1_domain/services/event_id_helper.py`
- Projector: `app/layer1_domain/services/projector/run_state_projector.py`
- Snapshot codec version gate: `app/layer1_domain/services/projector/snapshot_codec.py:26`
- Next DB migration: `db/migrations/029_*.sql` (after 028 snapshots)
- ResponseRegistry pattern (used for OutboxAwaiter): Phase 3c docs
  `phase-3c-run-lock-retirement.md` §4
