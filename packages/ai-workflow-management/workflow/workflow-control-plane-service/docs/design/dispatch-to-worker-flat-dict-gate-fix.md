# Fix: shadow/executor-mode `'url' is required` dispatch flake

**Status:** FIXED + live-verified (2026-06-09, 5/5 shadow-mode runs clean)
**Symptom:** under default `EXECUTOR_MODE=shadow`, HTTP TASK nodes intermittently failed
with worker error `'url' is required`, failing the whole run before downstream nodes ran.
Workaround had been `EXECUTOR_MODE=in_process`.

## Root cause

For every TASK node, **two** `NodeDispatched` events are emitted (confirmed via the run's
execution-log):

| seq | source | `dispatch_to_worker` | `input` |
|---|---|---|---|
| 3 | `task-creator` (`TaskCreatorConsumer`) | **`False`** | `{}` (empty) |
| 6 | `workflow-control-plane` (engine) | `True` | `{method, url, …}` |

The `task-creator` event is **tracking-only by design** — it announces the task lifecycle
to the projector/FE but must NOT be POSTed to the worker (it carries no resolved input).
`HttpTaskDispatcher` has a gate, `_should_dispatch_to_worker`, exactly for this. The gate
**misread the flag**:

```python
if isinstance(event, dict):
    return event.get("payload", {}).get("dispatch_to_worker", True)   # BUG
```

By the time the event reaches `HttpTaskDispatcher`, `EnrichingEventPublisher` has serialised
the dataclass with `dataclasses.asdict(event)` and forwarded the **flat dict** inward
(`enriching_event_publisher.py:230`). In that flat dict `dispatch_to_worker` is a
**top-level** key — there is no `payload` wrapper. So `event.get("payload", {})` → `{}` →
`.get("dispatch_to_worker", True)` → **`True`** → the empty event was dispatched to the
worker → `'url' is required`.

**Why it looked intermittent (a race):** both dispatches reached the worker; the run's fate
depended on which callback landed last — the good dispatch's `/complete` or the empty one's
`/fail`. Earlier run `ee657830` happened to win the race; most others lost it.

The dataclass branch of the gate was correct (`getattr(event, "dispatch_to_worker", True)`),
and the unit tests only ever exercised the **dataclass** path — which is why the suite
stayed green while production (flat-dict path) failed.

## Fix

`app/layer4_frameworks/messaging/http_task_dispatcher.py` — read the flag from the
top level of a flat dict first, fall back to a `payload`-wrapped envelope, default `True`:

```python
if isinstance(event, dict):
    if "dispatch_to_worker" in event:
        return bool(event["dispatch_to_worker"])
    return event.get("payload", {}).get("dispatch_to_worker", True)
```

The authoritative engine dispatch (`dispatch_to_worker=True`, flat dict) still returns
`True` → still dispatched. The `fallback.publish` (projector/FE tracking) is called
unconditionally before the gate, so lifecycle tracking is unaffected.

## Verification

- **Unit:** `tests/unit/test_http_task_dispatcher.py` +4 flat-dict cases (14 pass). The new
  tests fail against the old gate (flat dict `dispatch_to_worker=False` → was dispatched).
- **Live (shadow mode, the default):** 5 consecutive runs of workflow `10dea6aa…` —
  **5/5** TASKs COMPLETED, every run reached the INPUT-node PENDING state, worker log shows
  **5 GET dispatches and 0 `'url' is required`**. The race is gone because the failing
  dispatch is suppressed at the worker boundary.

## Sibling transports

The same gate exists per transport (`messaging/__init__.py` wraps whichever dispatcher is
active, all in the same chain position behind `EnrichingEventPublisher`):

- **`HttpTaskDispatcher`** (default `TRANSPORT_MODE=http`) — **fixed** + live-verified.
- **`GrpcTaskDispatcher`** — had the **identical** nested-`payload` gate bug; **fixed** the
  same way + flat-dict unit tests (skipped locally — `workflow_proto`/`grpcio` not
  installed; run where grpc deps exist). Affects `TRANSPORT_MODE=grpc`.
- **`EventMeshTaskDispatcher`** (`TRANSPORT_MODE=event_mesh`) — **structurally different,
  left as a TODO.** It has *no* `dispatch_to_worker` gate at all, and its
  `_to_dispatch_payload` reads `event.get("payload", {})` — i.e. it expects a
  *nested-envelope* event, so a flat `asdict` dict yields an empty payload (no
  `worker_type` → not dispatched). It needs its own review against the actual event shape
  it receives before asserting correctness; not exercised by the local/default stack.

## Notes (observations, not chased)

- `TaskCreatorConsumer` emitting for the *entry* node at all is a minor off-design wrinkle
  — its docstring says it skips when the `NodeInput` carries a `task_id`; that it fired
  means the entry `NodeInput` lacked one. The gate is still the correct fix: the
  `dispatch_to_worker=False` tracking event is *designed* to exist (for fan-out nodes) and
  be suppressed at the worker boundary regardless.
- This supersedes the `EXECUTOR_MODE=in_process` workaround noted in
  `input-node-empty-root-scalars.md`; shadow-mode runs are now reliable.
