# Executor: broker-transport performance + 512 MB capacity sizing

**Date:** 2026-06-10
**Questions:** (1) performance when task dispatch moves HTTP → message broker; (2) for a 512 MB BTP instance, how many consumers per executor instance.

## TL;DR
- The executor runs **exactly 1 broker consumer (one daemon poll thread) per instance — by design.** Not configurable without code.
- **Memory is not the binding constraint.** At the default `MAX_CONCURRENT_TASKS=100` the process needs ~180–230 MB of the 512 MB (~2× oversized). The binding constraint is the **single serial poll loop with an unconditional `sleep(0.5)` → ~2 tasks/s ingest per instance.**
- So the answer isn't "reduce consumers to fit memory." It's: **1 consumer/instance; scale throughput by adding instances** (safe competing-consumers on a shared queue).
- **Poll-loop fix shipped 2026-06-10** — removed the blind `sleep(0.5)` + added batch-drain, raising the per-instance ceiling from ~2 tasks/s to **~5–10 tasks/s** (now bounded by 3 serial Event Mesh REST calls/message on the single poll thread). Tunable via `BROKER_POLL_BATCH_SIZE` / `BROKER_POLL_IDLE_SLEEP_SECONDS`.

> **Current BTP state (dev/qas):** `MESSAGING_MODE=sap` but `TRANSPORT_MODE` unset → **`http`**. Tasks dispatch over HTTP today; the broker carries only results/events. "Move to the broker" = set `TRANSPORT_MODE=event_mesh`, which activates the poll-thread consumer below. `deployment/deployment.yml`: `memory: 512M`, `instances: 1`.

---

## 1. Performance — HTTP vs `event_mesh` task transport

| Dimension | HTTP (today) | Broker (`event_mesh`) |
|---|---|---|
| **Per-task latency** | ~single intra-region POST (~ms) each hop | **~1–2 s added round-trip** |
| **Throughput / instance** | bounded by async concurrency (`MAX_CONCURRENT_TASKS=100`) | **~5–10 tasks/s** after the 2026-06-10 poll-loop fix (was ~2/s); 1 poll thread, 3 serial REST calls/msg |
| **Per-task network** | 1 POST dispatch + 1 POST result | ≥4 EM REST calls (publish+consume+ack per hop ×2) + OAuth |
| **Coupling / durability** | synchronous; CP must reach executor; lost on crash | decoupled; queue buffers + survives restarts; back-pressure |
| **Scaling** | scale instances behind LB | competing consumers on one queue |
| **Delivery** | at-most-once (one shot) | at-least-once (+ the re-drive fix) |

**Why the latency:** broker adds, on **both** hops (dispatch *and* result), a poll-wait (~0.25 s avg, ≤0.5 s) plus Event Mesh REST round-trips (consume + ack + publish, each TLS + OAuth bearer) — versus HTTP's single intra-region POST per hop. The result hop is symmetric: executor → `workflow.results` → CP's poll-loop consumer.

**Why the throughput cap (pre-fix):** the old `_poll_loop` consumed **one message per cycle** and called `time.sleep(0.5)` **every** cycle (not just when idle) → ≤~2 msg/s regardless of `MAX_CONCURRENT_TASKS=100`. **Fixed 2026-06-10** (batch-drain + idle-only sleep) → ~5–10 msg/s; the residual ceiling is the 3 serial REST round-trips per message on the single poll thread, addressable only by concurrent handling (see §2).

**What broker buys:** decoupling, durability, back-pressure, elastic horizontal scaling, at-least-once.
**What it does NOT solve:** per-task latency (worse), single-instance throughput (much worse, current code), and the executor broker client's missing size-guard / retry / DLQ (separate gaps in `http-to-broker-migration-gaps.md`).

---

## 2. Capacity — 512 MB instance

### Consumers per instance = **1** (design)
`subscribe_to_topic` starts exactly one daemon poll thread (`event_mesh_broker_client.py:199-204`); it polls the single `workflow.tasks` subscription. There is no pool — one consumer per process.

### Memory budget (512 MB hard limit; CF OOM-kills at the limit, signal 137, no grace)
> **Get the real number first:** `cf app ai-workflow-worker-executor` (or BTP cockpit → app → memory) shows actual "X of 512M" on the live dev/qas instance. The breakdown below is the fallback estimate.

| Component | Est. RSS |
|---|---|
| Python 3.12 + interpreter | ~20–30 MB |
| FastAPI / Starlette / uvicorn / pydantic v2 | ~60–90 MB |
| httpx + prometheus + structlog | ~15–20 MB |
| hdbcli + SQLAlchemy + HANA pool (5 conns) *if active* | ~30–60 MB |
| `confluent-kafka` | **0** — not imported in `sap` mode (lazy import lives in the `local` branch) |
| app objects (container, stores, registry) | ~10–20 MB |
| **Baseline (idle)** | **~150–220 MB** |
| In-flight task state @ 100 (`MemoryPendingTaskStore` ~30–100 KB/task + transient httpx client/dispatch) | ~3–10 MB |
| `TaskTraceStore` (200-ring + active) | ~1–2 MB |
| **At full load (100 in-flight)** | **~185–230 MB** |

→ **Fits 512 MB with ~280–325 MB headroom — memory is ~2× oversized for this workload.** Single uvicorn async process (`main.py`), no gunicorn multiprocess. The concurrency cap (`ConcurrencyControl`) is a single-threaded counter — correct only because there's one async process + one poll thread.

### What memory could support (not what to set)
Headroom comfortably allows raising `MAX_CONCURRENT_TASKS` from 100 to ~**200–500** in-flight while keeping ~20–30 % free for GC / payload spikes. But there's no point: the serial consumer feeds only ~2/s, so 100 is already far more than it can fill.

### How to actually scale throughput
- **Horizontal (works today):** add instances. All instances bind the **same** queue (`{ns}/sub-workflow.tasks`, topic-derived — `event_mesh_broker_client.py:185`), so Event Mesh delivers each message to exactly one instance (competing consumers). **No duplicate execution** — safe.
- **Poll-loop fix — SHIPPED 2026-06-10:** the consumer now drains up to `BROKER_POLL_BATCH_SIZE` (default 50) messages per queue per pass with no inter-message delay, and sleeps `BROKER_POLL_IDLE_SLEEP_SECONDS` (default 0.5) **only when every queue is empty** (`event_mesh_broker_client._poll_once`). This removes the ~2 tasks/s artificial ceiling.
  - **Honest new ceiling: ~5–10 tasks/s per instance**, not the ~50–200 I first estimated. Each message still costs **3 serial Event Mesh REST round-trips on the single poll thread** — consume + dispatch-to-worker + ack (~100–200 ms total) — so throughput is now bounded by that serial REST cost (~3–5× better than the blind-sleep loop), not by an artificial sleep. (Estimate; measure real RTT against the live Event Mesh tenant.)
  - `instances ≈ ceil(target_tps / ~7)` after the fix (e.g. 50 tps → ~7 instances), vs `/2` before.
- **Concurrent consumers — SHIPPED 2026-06-10:** the consumer now runs `BROKER_CONSUMER_THREADS` (default 4) competing-consumer threads on the shared queue, parallelizing the consume+handle+ack REST round-trips. New ceiling ≈ **N × ~5–10 = ~20–40 tasks/s per instance** at the default N=4 (in-flight still bounded by `MAX_CONCURRENT_TASKS=100`, which the async fire-and-forget dispatch keeps well-fed). Raise N (or add instances) for more; each thread is cheap (shares the heap; the 512 MB headroom easily covers a handful).
  - Made safe by: per-message `x-lock-duration-in-ms` (`BROKER_LOCK_DURATION_MS`, default 60 s) so a message in flight isn't redelivered mid-process; **at-least-once skip-ack** (a handler exception → no ack → redeliver) so backpressure (`CONCURRENCY_LIMIT`) redelivers instead of dropping; and **`store_if_absent` dedup** so a redelivery never double-dispatches the worker.
  - **Concurrency race fixes (shipped same change; fix live bugs on the deployed HTTP path too):** `ConcurrencyControl` is now lock-guarded (was an unguarded check-then-increment, raced by FastAPI's sync-route threadpool); `MemoryWorkerRegistryStore` is RLock-guarded (was "dict changed size during iteration" between the poll thread and register/deregister); the sweep no longer double-releases when the callback completes a task first. See the race audit notes in memory.

## Key files
- `deployment/deployment.yml` (512M, instances:1, sap, REST), `app_config.py` (`MAX_CONCURRENT_TASKS=100`, `TASK_TIMEOUT_SECONDS=120`, `HANA_POOL_SIZE=5`)
- `app/layer4_frameworks/messaging/event_mesh_broker_client.py` (single poll thread :199-204, blind `sleep(0.5)` :246, queue name :185)
- `app/layer2_application/features/execute_task/use_cases/execute_task_usecase.py` (async dispatch + in-flight cap), `main.py` (single uvicorn process, consumer start)
