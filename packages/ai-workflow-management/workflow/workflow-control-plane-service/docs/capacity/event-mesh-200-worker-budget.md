# Event Mesh capacity budget — 200-worker scale (SA-1516)

Scenario under budget: **200 worker replicas + 2 control-plane + 2 executor**
instances on one SAP BTP sub-account. Companion to the topology ADR (SA-1515 —
**open**: numbers below are parameterized on its two candidate outcomes).

## Measured facts (tenant-1, 2026-07-02 — real, not assumed)

| Fact | Value | Source |
|---|---|---|
| Event Mesh service instances provisioned | **16** (`simplemdg-event-core-1..6`, `simplemdg-event-object-1..10`) | `cf services` |
| Plan | `default` (all 16) | `cf service <name>` |
| WCP binding | `simplemdg-event-core-2` (namespace `simplemdg/event/core-2`) | `cf env` VCAP |
| Queues currently provisioned by WCP | 6: `sub-orchestration-actions`, `sub-orchestration-responses-{shard}` (per CP instance), `sub-realtime-events`, `sub-catalog-changed`, `sub-workflow-results`, `publisher.dlq` | boot logs 13:47 |
| Consume transport | REST poll (explicitly configured; AMQP available in code) | `EVENT_MESH_CONSUME_TRANSPORT=rest`, SA-1470 readiness payload |
| Task dispatch transport | gRPC (HTTP fallback) — **NOT Event Mesh yet** | `TRANSPORT_MODE=grpc`; transport-audit (hackathon evidence) |
| Plan quota reference | SAP Help (per-plan queue/connection/rate limits) | doc link on every `cf service` output — **operator to transcribe exact numbers; NOT invented here** |

## Queue-count budget (per binding)

> **CORRECTED 2026-07-02 (Edgar):** the real scenario is **200 DISTINCT worker
> types** (heterogeneous, function-app/on-demand, 1 instance each) — so
> "per-node-type" and "per-worker" queues are the SAME 200-queue blowup. See
> SA-1515 comment + the `sa-1515-*-v2.png` diagrams.

`queues(binding) = fixed(5) + CP_instances(response topics) + DLQ(1)`

| Topology (SA-1515 v2) | Queues at 200 types | Verdict |
|---|---|---|
| **A. Executor-mediated invoke (ADR v2 default)** — mesh ends at the executor: `workflow.tasks` + `workflow.results` | 6 + 2 + 0 = **8** — independent of type count | ✅ Correct for on-demand types: HTTP invoke = wake; durability at the executor pending store |
| **B. Per-type mesh queues** (= per-worker at 1 instance/type) | 6 + 2 + 200 = **208** | ❌ Quota blowup AND strands messages while a worker is scaled to zero (no queue-depth wake trigger on CF) |
| **C. Selective mesh** — opt-in per type (long-running/burst/replay AND always-on) | 8 + K (K = registered exceptions, drawn from the 16-instance pool) | ⚠️ Allowed with registration in this doc |

## Connection budget

- Today (REST): HTTP pools only — no AMQP connections; budget consumed as **REST msg rate**, which is per service instance and shared by publish + consume + management.
- At 200 workers on EM: workers MUST use AMQP (SA-1470 mandate) — ~204 AMQP
  connections against per-instance connection caps → **shard across the existing
  16-instance pool** (see sharding decision).

## Traffic model (formula + measured anchor)

Per completed run with N task nodes (measured anchor: `hello` run = 1 node):
`events ≈ N×(dispatched+started+completed) × 2 (original+centralized) + run.status×2 + realtime ticks`
→ ~`8N + 6` publishes/run on WCP's binding. At the fan-out worst case
(loop ×100, parallel nodes) a single run bursts ~800+ publishes → the SA-1469
spool absorbs buffer-full without loss; sustained rate must stay under the
plan's per-instance REST cap (transcribe from SAP Help) with **≥2× headroom**.

## Sharding decision (recorded)

**Use the existing 16-instance pool via `BrokerRegistry` purposes** — the pool
already exists (measured), the routing mechanism already ships (`broker_registry.py`,
`BrokerPurpose`, secondary-binding support from SA-1466/M.3.3):

| Purpose | Instance (proposal) |
|---|---|
| orchestration (actions/results/tasks) | core-2 (current) |
| response_topics (per-CP) | core-3 (secondary binding — mechanism live) |
| realtime | core-4 |
| worker-tasks (future, 200-worker plane) | object-1..10 pool, per-node-type spread |

## Alert thresholds (derived from budget; rules live in the runbooks)

- `eventmesh.buffer.depth` > 80% of `EVENT_MESH_BUFFER_SIZE` → WARN (SA-1469)
- `eventmesh.consume.rate_limited` sustained 5 min → PAGE (SA-1470)
- `eventmesh.circuit.open` > 2 min → PAGE (SA-1471)
- Queue count per binding > 60% of plan quota → WARN (recheck before scaling replicas)

## Open dependencies (honest)

1. **SA-1515 ADR** must land to fix topology (this doc parameterizes both outcomes; only A is viable).
2. Exact `default`-plan numbers (queues/connections/msg-rate) to be transcribed
   from the SAP Help page into the table above by ops — deliberately not guessed.
3. Full-EM dispatch (TRANSPORT_MODE flip) blocked on health-check + gRPC-lifecycle
   fixes — see `transport-audit-tenant1.md` (hub hackathon evidence).

Refs: SA-1516 (parent SA-1302; blocked-by SA-1515; relates SA-1466).
