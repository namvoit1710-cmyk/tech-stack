# Phase 4 — Snapshot Store + Bounded-Tail Replay

> Give the projector a starting point other than "replay every event ever
> emitted for this run" on cold cache. Persist periodic `ProjectedRunState`
> snapshots keyed by `(run_id, sequence)`; on cold start, load the latest
> snapshot and replay only `sequence > snapshot.sequence` from the event
> log.

## 1. Why

### 1.1 The observable problem

`CachedProjectorRunContextViewStore` holds every projected run in an
unbounded in-memory dict (`cached_projector_view_store.py:94`). Pod
restart wipes the dict; the next `get(run_id)` call replays the **entire**
event log for that run via `event_log_repo.list_by_run()`
(`cached_projector_view_store.py:128-139`).

For long-lived runs (HITL pauses, queued-mode loops, multi-day
workflows) the event log grows O(nodes × attempts). Today this is
tolerable because:
- Mock/memory mode flushes the log on restart anyway.
- Production volume is small — single-pod, few concurrent runs.

Both assumptions break post-M.3. With multi-instance deployments:
- Every pod that handles a new `run_id` pays a full-replay cost on first
  touch (projector cache is per-pod, not shared).
- Sticky routing (M.1) reduces this but doesn't eliminate it — failover,
  rolling deploys, and scale-out each trigger a cold replay burst.
- Projector-authoritative reads (Phase 1b) make the cold-replay cost
  part of the **request-serving path**, not a background task.

### 1.2 Why now

Phase 3b made the projector authoritative for run state. Phase 3c
retired the in-process `RunLockRegistry` so per-run state now lives only
in the projector view. Phase M.3 enabled multi-instance deployments. All
three together mean cold-replay latency is now a production-visible
metric, not an implementation detail.

Phase 4 **does not change correctness**. The event log is already the
source of truth (durable HANA `AIW_EVENT_LOG(RUN_ID, SEQUENCE)` since
Phase 0). Snapshots are a pure performance optimisation on top of an
already-correct replay model. Worst case if the snapshot store
malfunctions: full-log replay, same as today.

### 1.3 Side benefit — forensic mode

Once snapshots exist, "what did this run look like at sequence N?"
becomes a cheap query for oncall debugging. Not a Phase 4 deliverable,
but the primitive falls out of the design.

## 2. Non-goals

- **Cache eviction (LRU on the in-memory cache).** Separate concern; the
  unbounded `_cache` dict holds full `ProjectedRunState` objects and
  should grow an LRU bound eventually, but that's orthogonal to the
  snapshot store.
- **Event-log compaction.** Kafka retention + HANA retention policies
  handle this at the storage layer. Phase 4 only reads from the log.
- **Cross-region snapshot replication.** Ops concern. Snapshots live
  next to the event log; whatever replicates one replicates the other.
- **Snapshot-based audit / point-in-time queries as a user-facing API.**
  Forensic replay stays an internal oncall tool in Phase 4.
- **Rebuilding `dag_info` / `input_data` from events.** Those are already
  sourced from `workflow_repo` / `run_repo` via the ProjectedView wrapper
  (`cached_projector_view_store.py:105-108`). Snapshots persist the
  event-derived state only.

## 3. What ships

Six sub-tasks, rollout-ordered. Each is small enough to PR individually;
4.1-4.4 can land behind `SNAPSHOT_MODE=off` with zero behaviour change.

| Sub | Content | LoC | Behavior change |
|-----|---------|-----|-----------------|
| **4.1** | `ISnapshotRepository` interface; memory impl; `Snapshot` envelope dataclass | ~80 | None — plumbing only, nothing wired |
| **4.2** | `ProjectedRunState` ↔ JSON serialiser with explicit frozenset/dict rehydration; `SCHEMA_VERSION` constant | ~120 | None — pure function tested in isolation |
| **4.3** | `SnapshotWritingViewStore` decorator around `CachedProjectorRunContextViewStore`; `SNAPSHOT_EVERY_N_EVENTS` / `SNAPSHOT_EVERY_T_SECONDS` policy | ~150 | Writes snapshots when `SNAPSHOT_MODE ∈ {shadow, authoritative}`; still replays full log on cold start |
| **4.4** | Cold-start replay path checks snapshot repo first, falls back to full replay on miss/mismatch | ~80 | Under `SNAPSHOT_MODE=authoritative`, cold start is O(Δ) instead of O(total events) |
| **4.5** | HANA snapshot repository + migration `028_create_aiw_run_snapshots.sql`; wired in `bootstrap.py` | ~200 | Snapshots durable in SAP infra mode |
| **4.6** | Shadow parity harness: both paths replay every cold start, compare final state, log drift | ~120 | Verification only; removed at end of soak |

**Total:** ~750 LoC + 1 migration + test files.

## 4. Design

### 4.1 The snapshot envelope

```python
@dataclass(frozen=True)
class Snapshot:
    run_id: str
    sequence: int           # last-applied event sequence
    schema_version: int     # bumped when reducer changes semantics
    state_json: str         # serialised ProjectedRunState
    created_at: str         # ISO8601 UTC
```

`schema_version` is the guard against reducer changes. When the Phase 5
reducer adds a field or changes how an event folds, the constant bumps
and old snapshots fall back to full replay automatically. No manual
migration of old snapshots — cheaper to just re-derive than to rewrite
them.

### 4.2 Repository interface

```python
class ISnapshotRepository(Protocol):
    def save(self, snapshot: Snapshot) -> None: ...
    def load_latest(self, run_id: str, *, min_schema_version: int) -> Snapshot | None: ...
    def delete_older_than(self, run_id: str, keep_sequence: int) -> None: ...
```

- `save` is idempotent on `(run_id, sequence)`. Upsert in HANA; same-key
  writes are no-ops or just refresh `created_at`.
- `load_latest` returns the highest-sequence snapshot whose
  `schema_version ≥ min_schema_version`. Below-threshold snapshots are
  ignored but not deleted (the deletion happens at next `save`).
- `delete_older_than` is the retention hook — keep the last K snapshots,
  drop the rest. K=2 default (one current, one fallback).

Memory impl for tests/mock mode stores a per-run list; HANA impl uses
`AIW_RUN_SNAPSHOTS(RUN_ID, SEQUENCE, SCHEMA_VERSION, STATE_JSON NCLOB,
CREATED_AT TIMESTAMP, PRIMARY KEY (RUN_ID, SEQUENCE))`.

### 4.3 Serialisation

`ProjectedRunState` is a `@dataclass(frozen=True)` — `dataclasses.asdict`
+ `json.dumps` handles most of it. The three fields that need care:

- `completed_nodes: frozenset[str]` → JSON array, rehydrated via
  `frozenset(data["completed_nodes"])`.
- `skipped_nodes: frozenset[str]` → same.
- `variables: dict[str, Any]` and `input_data: dict[str, Any]` — these
  contain `__file_ref` dicts with nested artifacts. JSON round-trip is
  fine since `__file_ref` is already a dict, not a dataclass.

Same rehydration pattern we used for `EditNodeDataResult` in M.3
(`_rehydrate_edit_node_data_result` in
`response_topic_listener.py`). Colocate as
`app/layer1_domain/services/projector/snapshot_codec.py` with
`encode_state(state) -> str` / `decode_state(json_str) -> ProjectedRunState`
as pure functions.

### 4.4 Write policy — when to snapshot

Triggered from inside `get(run_id)` **after** a successful read that
advanced the cache. Two thresholds, whichever hits first:

- `SNAPSHOT_EVERY_N_EVENTS` (default **100**): snapshot if
  `current_sequence - last_snapshotted_sequence ≥ N`.
- `SNAPSHOT_EVERY_T_SECONDS` (default **300**): snapshot if
  `now - last_snapshotted_at ≥ T` AND at least one new event was folded
  since the last snapshot.

Rationale: N-events catches high-throughput runs (lots of fan-out),
T-seconds catches slow long-lived runs (HITL waits, sparse activity)
that would otherwise never hit N before a pod restart.

Additionally, **always** snapshot on terminal status transition
(`RunCompleted`, `RunFailed`, `RunCancelled`) — a final snapshot makes
post-mortem replay of historical runs O(1) regardless of log length.
Trigger detected in the decorator by checking `state.status` in
`{COMPLETED, FAILED, CANCELLED}` after fold.

Writes are **fire-and-forget async**. The snapshot write runs on an
asyncio task; read latency is unaffected. A failed write logs a warning
and increments a counter — next `get()` call that crosses the threshold
will retry.

### 4.5 Cold-start replay path

Modified `_cold_load(run_id)` (extracted from inline
`cached_projector_view_store.py:128-139`):

```
if snapshot_repo and mode == authoritative:
    snap = snapshot_repo.load_latest(run_id, min_schema_version=CURRENT)
    if snap:
        state = decode_state(snap.state_json)
        tail = event_log_repo.list_by_run_since(run_id, snap.sequence)
        for env in tail:
            state = apply_event(state, env)
        cache[run_id] = (state, tail[-1].sequence if tail else snap.sequence)
        return wrap(state)
# Fallback — no snapshot, schema mismatch, or shadow/off mode
envelopes = event_log_repo.list_by_run(run_id)
...  # existing path
```

Under `SNAPSHOT_MODE=shadow`, **both paths run** per cold start and a
comparator asserts the two states are equal (§4.6 below). Authoritative
mode trusts the snapshot path and skips the comparator.

### 4.6 Shadow comparator

```python
class SnapshotShadowComparator:
    def compare(self, run_id: str, from_snapshot: ProjectedRunState,
                from_full_replay: ProjectedRunState) -> list[str]:
        # returns list of diverged field names
```

Triggered only when `SNAPSHOT_MODE=shadow` and both a snapshot and the
full replay are available. Logs divergences at WARN with
`run_id`, `snapshot_sequence`, `diverged_fields`. Stored in a
`snapshot_drift_metrics` counter for ops dashboard. Drift=0 over the
soak window is the flip gate.

### 4.7 Retention

On each `save`:
1. Upsert the new snapshot.
2. Call `delete_older_than(run_id, new_sequence - SNAPSHOT_KEEP_K_BACK)`
   where K=2 default. Keeps current + one fallback; dumps the rest.

For terminal runs: one final snapshot, all older ones dropped. Implicit
from the same rule — the terminal snapshot's sequence is the run's max,
so everything before it falls outside `keep_sequence`.

Size bound: per run, **at most 2 snapshots** at any time (3 briefly,
during upsert). For 10k active runs with ~5 KB per `state_json`, steady
state is ~100 MB in the snapshot table — trivial.

## 5. Fast / slow paths

### 5.1 Memory mode (`INFRA_MODE=mock`)

- No snapshot repo wired (`infra.snapshot_repo is None`).
- Decorator is a pass-through — behaviour identical to Phase 3b.
- Zero overhead.

### 5.2 Single-pod HANA, steady state

- Snapshot every 100 events / 5 min.
- Reads: warm cache, no replay. Snapshot path never hits.
- Writes: one snapshot row per threshold crossing. Negligible.

### 5.3 Pod restart, authoritative mode

- First `get(run_id)`: `load_latest` hits HANA (indexed lookup on
  `(RUN_ID, SEQUENCE DESC)`), decodes JSON, pulls 0-100 tail events,
  folds. Total: one HANA read + one event-log read + O(Δ) folds.
- vs. current: one event-log read of the **entire** log per run + O(N) folds.
- Improvement scales with log length. For a run with 5000 events: from
  ~5000 folds to ≤100.

### 5.4 Schema mismatch after a reducer change

- `SCHEMA_VERSION` bumps in code.
- All snapshots with lower version are ignored by `load_latest`.
- Full replay falls back automatically.
- Next write creates a new-version snapshot; old ones are swept by
  `delete_older_than` on subsequent saves.
- **No manual backfill needed.** The system self-heals in one snapshot
  cycle per run.

## 6. Hazards + mitigations

### 6.1 Snapshot written during concurrent fold

The `get()` method takes a threading lock around cache mutation
(`cached_projector_view_store.py:109, 124, 138`). Snapshot write must
happen **outside** the lock — asyncio.create_task after the lock
releases, using a snapshotted copy of the state (it's frozen, so a
reference suffices). No shared mutable state between write and reads.

### 6.2 Async write fails mid-flight

- Log the exception, increment `snapshot_write_failures` counter.
- Do not retry inline — the next threshold crossing tries again.
- Worst case: snapshot lag. Falls through to full replay on cold start,
  which is correct just slow. Same as `SNAPSHOT_MODE=off`.

### 6.3 Snapshot and event log diverge

**Can't happen by construction** — the snapshot is `apply_event`-derived
from the same log. If the reducer is deterministic (it is — pure
function of envelope + previous state), both paths produce identical
states for the same event sequence.

The shadow comparator exists to catch reducer-behaviour drift across
code versions (someone changes `apply_event` without bumping
`SCHEMA_VERSION`). That's a code-review bar, not a runtime one.

### 6.4 Terminal-snapshot race with post-terminal events

Some event types arrive **after** `RunCompleted` — late
`TaskCompleted` from a worker that raced the cancellation, for example.
The reducer already handles these idempotently (folds go into
`completed_nodes`, status stays terminal). The terminal snapshot
captures state at the moment of transition; late events fold on top of
the snapshot at next read and trigger another snapshot if the threshold
is hit. Not a bug.

### 6.5 Sequence gaps from replay skip

`list_by_run_since(run_id, N)` returns events with `sequence > N`. If
the snapshot claims `sequence=100` but the event log has a gap (99 was
never persisted due to a crash between publish and log append), the
fold skips the gap without noticing. Current system has the same
exposure — snapshots don't make it worse.

Mitigation is at the publish layer (Phase 0 invariant:
`EventLogAppendingPublisher` appends before inner publishers run), not
Phase 4.

### 6.6 Forensic replay sees stale snapshot

If someone writes a forensic tool that loads a snapshot and asks "what
was the state at sequence N?" where N < snapshot.sequence: undefined —
snapshots are not point-in-time, they're "latest ≤ here." Forensic mode
should always take a lower-bound sequence and replay from an earlier
snapshot (or from zero). Document this on the forensic tool when it
ships; not a Phase 4 concern.

## 7. Configuration

New `settings` fields (`app/layer4_frameworks/config/app_config.py`):

```python
SNAPSHOT_MODE: str = os.getenv("SNAPSHOT_MODE", "off")
# "off" | "shadow" | "authoritative"

SNAPSHOT_EVERY_N_EVENTS: int = int(os.getenv("SNAPSHOT_EVERY_N_EVENTS", "100"))
SNAPSHOT_EVERY_T_SECONDS: int = int(os.getenv("SNAPSHOT_EVERY_T_SECONDS", "300"))
SNAPSHOT_KEEP_K_BACK: int = int(os.getenv("SNAPSHOT_KEEP_K_BACK", "2"))
```

Defaults are conservative. Ops can tune per-environment.

## 8. Test matrix

| Test file | Purpose | Count |
|-----------|---------|-------|
| `test_snapshot_codec.py` | JSON round-trip fidelity: `decode(encode(state)) == state` for all `ProjectedRunState` shapes including frozensets and nested `__file_ref` dicts | 8 |
| `test_snapshot_repository.py` | `save`/`load_latest`/`delete_older_than` semantics on memory impl; schema-version filtering; K-back retention | 10 |
| `test_snapshot_writing_view_store.py` | Threshold triggers (N events, T seconds, terminal status); fire-and-forget write doesn't block read; write failure logged not raised | 12 |
| `test_cold_start_replay.py` | Authoritative mode: snapshot + tail = full replay; shadow mode: comparator catches synthetic divergence; off mode: unchanged behaviour | 8 |
| `test_hana_snapshot_repository.py` (integration, SAP mode) | HANA upsert semantics; migration 028 applies clean; schema matches | 6 |
| `test_snapshot_property.py` | Property test: fold N events → snapshot → fold M more ≡ fold N+M events | 1 (hypothesis) |

**Expected delta:** +45 tests. Zero changes to existing tests (behaviour
unchanged under `SNAPSHOT_MODE=off`, which is the default).

## 9. Rollout

Four gates, each ≥ 1 week apart:

1. **Gate 0 — merge, `SNAPSHOT_MODE=off` default.** All code lands but
   the decorator is a pass-through. Verify: test suite green + existing
   cold-replay latency unchanged.
2. **Gate 1 — `SNAPSHOT_MODE=shadow` in staging.** Both paths run on
   every cold start; comparator asserts equality. Verify: drift=0 for
   7 days under representative load.
3. **Gate 2 — `SNAPSHOT_MODE=shadow` in prod.** Same. Verify: drift=0
   for 14 days; snapshot write latency < 50 ms p99; snapshot table
   size grows linearly with run count, not event count.
4. **Gate 3 — `SNAPSHOT_MODE=authoritative`.** Flip staging first, then
   prod after 1 week. Verify: cold-replay latency drops
   (snapshotted-run p99 < 200 ms vs. pre-Phase-4 p99 ≥ log-length-bound).

Rollback at any gate: flip `SNAPSHOT_MODE` back to `off` or `shadow`.
Snapshot rows remain in HANA but become inert.

## 10. Sub-task decomposition

### 4.1 — Interface + memory impl

**Files:**
- `app/layer2_application/interfaces/snapshot_repository_interface.py` (NEW)
- `app/layer4_frameworks/persistence/memory/memory_snapshot_repository.py` (NEW)
- `tests/unit/test_snapshot_repository.py` (NEW)

**Exit:** Memory repo passes save/load/retention unit tests. Not wired.

### 4.2 — Serialisation codec

**Files:**
- `app/layer1_domain/services/projector/snapshot_codec.py` (NEW)
- `tests/unit/test_snapshot_codec.py` (NEW)

**Exit:** Round-trip equality for all event-fold outputs in
`test_run_state_projector.py` fixtures. `SCHEMA_VERSION = 1` constant.

### 4.3 — Writing decorator

**Files:**
- `app/layer4_frameworks/run_context/snapshot_writing_view_store.py` (NEW)
- `app/layer4_frameworks/config/app_config.py` (MODIFY — 4 settings)
- `bootstrap.py` (MODIFY — wire decorator when `infra.snapshot_repo is not None`)
- `tests/unit/test_snapshot_writing_view_store.py` (NEW)

**Exit:** With memory snapshot repo + `SNAPSHOT_MODE=shadow`, snapshots
written at threshold crossings. Existing integration tests pass
unchanged.

### 4.4 — Cold-start replay path

**Files:**
- `app/layer4_frameworks/run_context/cached_projector_view_store.py` (MODIFY — accept snapshot_repo, branch in cold path)
- `tests/unit/test_cached_projector_view_store.py` (EXTEND)
- `tests/integration/test_cold_start_replay.py` (NEW)

**Exit:** Authoritative mode cold-replay path verified — snapshot +
tail yields identical state to full replay on a 500-event synthetic
run.

### 4.5 — HANA backend

**Files:**
- `db/migrations/028_create_aiw_run_snapshots.sql` (NEW)
- `app/layer4_frameworks/persistence/hana/hana_snapshot_repository.py` (NEW)
- `bootstrap.py` (MODIFY — construct HANA repo under `INFRA_MODE=sap`)
- `tests/integration/test_hana_snapshot_repository.py` (NEW, SAP-mode-only)

**Exit:** HANA repo passes the same contract tests as the memory impl.
Migration applies to a clean HANA schema without errors.

### 4.6 — Shadow parity harness

**Files:**
- `app/layer1_domain/services/projector/snapshot_shadow_comparator.py` (NEW)
- `app/layer4_frameworks/monitoring/snapshot_drift_metrics.py` (NEW — counter bundle)
- `tests/integration/test_snapshot_shadow_parity.py` (NEW)

**Exit:** Synthetic divergence (mutate one snapshot's `completed_nodes`)
is caught and logged. Drift counter increments.

## 11. What Phase 4 does NOT do

- **No changes to `apply_event`.** The reducer stays pure and unchanged.
- **No new event types.** Snapshots piggyback on the existing event log.
- **No changes to the HTTP / Socket.IO surface.** User-observable
  behaviour is unchanged.
- **No kafka topic changes.** Snapshots live in HANA; they're a
  materialised view of the log, not a parallel stream.
- **No projector service extraction.** That's still a Phase 1 / future
  concern; Phase 4 operates on the in-process projector.

## 12. Open questions

1. **Compression for `STATE_JSON`?** Current sizes are tiny (< 10 KB for
   most runs) but queued-mode loops with large `variables` payloads
   could grow. Defer — revisit if profiling shows > 100 KB typical.
2. **Snapshot on `RunPaused` (HITL wait)?** A long-paused run is the
   exact case where the T-second timer triggers, so no special-casing
   needed. But a pause-triggered snapshot would guarantee the HITL
   wait-state is cheap to rehydrate. Cheap to add — revisit during 4.3.
3. **Who drives the `delete_older_than` for abandoned runs?** If a run
   stops producing events entirely, its snapshot lingers forever.
   Retention sweep by run-terminal-status + age is simple but requires
   a scheduled job. Defer — trivial compared to event-log retention,
   which has the same shape.

## 13. Appendix — Reference points in current code

- Projector reducer: `app/layer1_domain/services/projector/run_state_projector.py:112-235`
- `ProjectedRunState` dataclass: `run_state_projector.py:63-82`
- View-store cache + cold/warm paths: `app/layer4_frameworks/run_context/cached_projector_view_store.py:68-140`
- Event log interface: `app/layer2_application/interfaces/event_log_repository_interface.py:34-51`
- Event log HANA schema: `db/migrations/025_create_aiw_event_log.sql`
- Bootstrap wiring point: `bootstrap.py:117-156`
- Next migration number: **028**
