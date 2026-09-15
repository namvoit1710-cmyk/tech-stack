# Follow-up ticket — Signal B: per-task lease / heartbeat for dispatched tasks

**Type:** Story (reliability) · **Parent epic:** SA-1010 · **Status:** proposed (not started)
**Relates:** the dispatch fail-fast guard + liveness sweep shipped alongside this doc
(Layer 1 + Layer 3 / Signal A).

## Problem — the gap Signal A cannot close

The liveness sweep (`sweep_unservable_dispatched_tasks`, Signal A) fails a dispatched
TASK only when its `worker_type` has **zero** live workers in the registry. That covers:

- a mis-typed `builtin:*` that slipped the dispatch guard (Layer 1 already fails these), and
- a whole worker fleet for a type being down / undeployed.

It intentionally does **not** fail a task whose type still has ≥1 live worker — that is
what protects legitimately long-running tasks. But it leaves one real failure mode open:

> **One worker instance claims a task and dies mid-execution while sibling instances of
> the same type stay alive.** The registry still reports the type as "live", so Signal A
> never sweeps the orphaned task. It sits RUNNING until an operator intervenes.

Why Signal A structurally can't catch this: today a `Task` records **neither** which
worker instance picked it up **nor** any per-task progress. The control plane cannot tell
"instance A is still chewing on a long task" from "instance A died and its task is
orphaned" — from the type's liveness alone, both look identical.

## Proposed design — claim + lease renewal (SQS visibility-timeout / Temporal heartbeat)

Give each dispatched task an explicit, self-renewing liveness signal owned by the worker
that is actually running it:

1. **Claim.** When a worker accepts a dispatched task it records a claim: `task.worker_id`
   (the instance id) + `task.lease_expires_at = now + lease_ttl`.
2. **Renew.** While executing, the worker renews the lease every `lease_ttl/3` (a cheap
   callback / heartbeat carrying `task_id` + `worker_id`). Each renewal pushes
   `lease_expires_at` forward. A long-but-alive task keeps renewing → its lease never
   expires → it is **never** false-failed, regardless of total duration.
3. **Expire → reclaim/fail.** A sweep (extend the same 5–10 s loop) finds RUNNING tasks
   whose `lease_expires_at` is in the past and whose `worker_id` no longer matches a live
   worker → the claiming instance is gone. Fail (or re-dispatch, once) via the canonical
   `FailTaskUseCase` path, exactly as Signal A does.

This is the only mechanism that **separates "dead" from "long"**: liveness is proven
per-task by active renewal, not inferred from wall-clock duration or type-level presence.

## Scope / changes

- **worker-sdk (protocol):** claim-on-accept + periodic lease renewal. Per the standing
  rule, wire **both** the sync (`/execute`, gRPC `Execute`, shared use case) **and** async
  (`/execute-async` + callback) paths — async first. (See memory
  `worker-sdk-sync-async-both`.)
- **CP domain:** add `worker_id` + `lease_expires_at` to `Task`; a `renew_task_lease`
  callback endpoint/use case; extend the sweep to key off lease expiry.
- **Config:** `WORKER_TASK_LEASE_TTL_SECONDS` (default generous, e.g. 120), renewal
  interval derived; opt-in gate consistent with Signal A.
- **Registry:** confirm `list_workers()` exposes a stable per-instance id to match
  `task.worker_id` against (needed for "claiming instance is gone").

## Acceptance criteria

- A worker that dies mid-task (stops renewing) has its task failed/re-dispatched within
  ~`lease_ttl`, **even when other instances of the same type are alive**.
- A task that keeps renewing is never failed, no matter how long it runs (no false-fail of
  long work) — verified with a multi-minute task under a short lease TTL.
- Claim/renew covers both sync and async execution paths.
- Feature is opt-in; default behavior unchanged.

## Effort / risk

L (worker-sdk protocol + CP schema + sweep). Higher blast radius than Signal A because it
touches the worker execution protocol — ship behind a flag and roll out per worker type.

## Why deferred (not in this change)

Signal A (liveness) + the Layer 1 dispatch guard already remove the *silent, permanent*
hang for the realistic cases (no worker for a type / mis-typed builtin) with **zero**
false-positive risk to long tasks and **no** protocol change. Signal B is the complete
fix for single-instance-death-mid-task and is a protocol-level effort worth its own story.
