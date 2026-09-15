# Pull-based worker delivery (SA-2025) — operator guide

The executor delivers a task to a worker in one of two ways, chosen **per registered
worker** by its `delivery_mode`:

| Mode | How | When |
|---|---|---|
| **push** (default) | executor `POST`s `/execute-async` to the worker's URL + gets a callback | worker is reachable from the executor (in-cluster) |
| **pull** | executor **stores** the task; the worker **leases** it, runs it, reports to the same `/tasks/callback` | worker is firewalled / on-prem / scale-to-zero / untrusted |

Pull is **purely additive**: `delivery_mode` defaults to `push`, and with the feature
flag off every push code path is byte-for-byte unchanged.

---

## 1. Enabling pull

Pull is gated by one flag on the executor:

```
PULL_DELIVERY_ENABLED=true
```

When `false` (default) the ready-task store is never created, `ExecuteTaskUseCase`
receives `ready_task_store=None`, and the pull branch is skipped entirely — a push-only
deployment pays nothing.

A worker opts into pull at registration (`delivery_mode=pull`); the SDK sets this when
run with `APP_MODE=PULL`. WCP dispatch is unchanged — it does not know or care whether a
worker is push or pull.

## 2. Endpoints (all under `/api/v1`)

| Method · path | Who | Purpose |
|---|---|---|
| `POST /workers/register` | worker | register with `delivery_mode: pull` |
| `POST /workers/type/{type}/tasks/lease?max&wait` | pull worker | long-poll to lease ≤`max` tasks from its `(tenant, type)` partition |
| `POST /tasks/{id}/lease/renew` (`X-Lease-Token`) | pull worker | extend a long-running task's lease |
| `POST /tasks/callback` (`X-Lease-Token`) | pull worker | report the result (shared with push) |
| `GET /workers/type/{type}/queue-stats` | autoscaler / ops | the KEDA scale-from-zero signal |
| `POST /runs/{run_id}/tasks/purge` | WCP | drop a cancelled/reset run's pull tasks |

Lease tokens are **header-only** (`X-Lease-Token`), never in a URL. All pull routes must
sit **behind the api-gateway** (XSUAA) — the gateway injects `X-Tenant-Id` from the
authenticated subject; the executor never trusts a tenant from a query param.

## 3. Autoscale (scale-from-zero)

A KEDA (or any) scaler polls `queue-stats` and scales the pull worker deployment on
`admitted_depth` (the leasable backlog) — **including up from zero**, since a
scaled-to-zero worker isn't long-polling to create demand any other way. Metrics:
`wes_pull_admitted_depth{worker_type,tenant}` and `wes_pull_leased{...}` are refreshed
each sweep (drained partitions are zeroed so the worker can scale back to zero).

A worker that never scales up cannot silently hang a run: a task READY longer than
`LEASE_MAX_READY_SECONDS` is terminated as **`WORKER_UNAVAILABLE`** (non-retryable) and
reported to WCP.

---

## 4. The three things an operator MUST understand

### 4a. It is resource-**reservation**, not **isolation**

Pull runs **in the same executor process** as push (the "(a) bound-and-share" model).
Pull's shared-resource footprint is strictly **bounded** so push keeps headroom:

- byte-quota hierarchy on the ready store — `MAX_READY_BYTES_{GLOBAL,PER_TENANT,PER_PARTITION}`
- a hard leased ceiling — `MAX_TOTAL_LEASED`
- a per-`(tenant,type)` long-poll connection cap — `MAX_LEASE_CONNECTIONS`
- a registry cap — `MAX_REGISTERED_WORKERS` (never evicts a live worker)

An admission-full enqueue applies **Event-Mesh backpressure** (redelivery), never a 429
or a silent drop. But under **genuine overload** there is residual **shared-fate**: pull
and push contend for the one process. If you need a hard isolation SLA, the graduation
path is **(b) a separate pull-executor deployable** — identical code + state machine,
just deployed apart. Do not treat the bounds as isolation.

### 4b. Idempotency is MANDATORY — the executor is NOT exactly-once

Pull gives **at-most-once *completion*, NOT exactly-once *execution***. A task's side
effects can run **more than once**:

- **lease overlap** — a slow-but-alive worker's lease expires, the task is re-leased to
  another worker, and both run it (the late one's callback is rejected as
  `TASK_SUPERSEDED`, but its side effects already happened);
- **executor restart** — in-flight leased tasks may be re-driven.

Therefore every pull task **must** carry a **stable idempotency key** (WCP mints it =
`f(run_id, node_id, run_generation)`, preserved across every re-enqueue/re-lease/re-drive)
and the worker's side effects must be **idempotent** or downstream-fenced on it. The
executor echoes `idempotency_key` + `run_generation` on every result event so WCP dedups.
Do not deploy a pull worker whose work is not idempotent.

### 4c. Cancellation is best-effort

A customer-hosted worker's **in-flight compute cannot be stopped remotely**. On
cancel/reset, WCP calls `purge_by_run` (the task can no longer be leased or completed)
and lease/generation supersession rejects a stale result — but a task **already running**
in a worker runs to completion locally; only its *reporting* is suppressed.

**Isolation unit** is `(tenant, worker_type)`, not the individual worker process: a
per-worker XSUAA credential is scoped to `(tenant, type)`, and a lease draws only from
the caller's `(tenant, type)` partition.

---

## 5. Config reference (executor)

| Env | Default | Meaning |
|---|---|---|
| `PULL_DELIVERY_ENABLED` | `false` | master gate |
| `LEASE_TTL_SECONDS` / `LEASE_TTL_OVERRIDES` | `60` / `""` | lease visibility timeout (empty = none; override is JSON `{"type": seconds}`) |
| `LEASE_MAX_WAIT_SECONDS` | `20` | long-poll hold cap |
| `LEASE_MAX_BATCH` | `16` | tasks per lease call cap |
| `MAX_TOTAL_LEASED` | `500` | hard in-flight leased ceiling (0 = unlimited) |
| `MAX_READY_BYTES_{GLOBAL,PER_TENANT,PER_PARTITION}` | `0` | admission byte quotas (0 = unlimited) |
| `MAX_LEASE_CONNECTIONS` | `0` | per-partition long-poll connection cap (0 = unlimited) |
| `MAX_REGISTERED_WORKERS` | `0` | registry cap (0 = unlimited; never evicts a live worker) |
| `MAX_LEASE_ATTEMPTS` | `5` | re-lease attempts before poison→DLQ (0 = unbounded) |
| `LEASE_BACKOFF_BASE_SECONDS` | `2` | exponential re-enqueue backoff base |
| `LEASE_MAX_READY_SECONDS` | `900` | READY-too-long → `WORKER_UNAVAILABLE` (0 = off) |

## 6. SDK (pull worker) config

`APP_MODE=PULL`, `REGISTRY_URL` (the executor), `WORKER_TENANT`, `LEASE_MAX_BATCH`,
`LEASE_WAIT_SECONDS`, `LEASE_IDLE_FLOOR_SECONDS` (anti-hot-spin), and — for long tasks —
`LEASE_RENEW_ENABLED=true` + `LEASE_RENEW_SAFETY_FRACTION` (the renew interval is derived
from the executor's authoritative lease TTL, so it's always below it — no cross-service
mis-config).

Full design + red-team record: `hackathons/worker-pull-delivery-permanent/` in the hub.
