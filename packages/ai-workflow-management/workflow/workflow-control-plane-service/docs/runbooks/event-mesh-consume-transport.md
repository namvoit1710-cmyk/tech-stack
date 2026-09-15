# Runbook — Event Mesh consume transport + 429 isolation (SA-1470)

The control plane consumes via **AMQP push** (preferred — no consumption-endpoint
429s) or the legacy **REST poll loop**. AMQP→REST fallback is automatic and
sticky for the process lifetime; before SA-1470 it was silent, and one REST 429
paused ALL queues with no metric.

## Metrics (via IMonitor)

| Metric | Type | Meaning |
|---|---|---|
| `eventmesh.consume.transport.rest` | gauge | 1.0 while consuming over REST, 0.0 on AMQP |
| `eventmesh.consume.fallback` | counter | AMQP could not be attached (`reason` = proton_missing / amqp_unreachable) |
| `eventmesh.consume.standdown` | counter | this pod is NOT consuming a topic (`queue`, `reason`). `reason=peer_owns_queue` is healthy — another instance has it. The other reasons are faults |
| `eventmesh.consume.rate_limited` | counter | a 429 engaged a cooldown (tags: queue, streak, scope) |
| `eventmesh.consume.cooldown_seconds` | track | duration of each engaged cooldown (bounded ≤ 30s) |

## Alert rules

1. **WARN** — `eventmesh.consume.transport.rest == 1` sustained while `EVENT_MESH_CONSUME_TRANSPORT=amqp`: the pod is on the REST fallback (429-prone). Investigate AMQP reachability (`amqp10ws` endpoint, proton libs in the image).
2. **PAGE** — `increase(eventmesh.consume.rate_limited) > 0` for > 5 minutes: sustained broker throttling; the engine's consumption is stalling in bounded pauses. Consider AMQP, reduce poll pressure, or shard instances (SA-1516).
3. Every cooldown is **time-bounded**: `Retry-After` else exponential 1s→30s cap. There is no unbounded pause.

## Config

| Env | Default | Notes |
|---|---|---|
| `EVENT_MESH_429_COOLDOWN_SCOPE` | `instance` | SAP EM's REST limit is per service instance → one cooldown pauses every queue (honest default). `queue` pauses only the throttled queue — use where limits are actually per-queue |
| `EVENT_MESH_REQUIRE_AMQP` | `false` | `1` ⇒ a pod forced onto REST reports **503 on `/api/v1/health/readiness`** (`event_mesh_rest_fallback` reason) so the platform pulls it. Flip on per environment once AMQP is verified there — same rollout pattern as SA-1466's gate |
| `EVENT_MESH_RUNTIME_FALLBACK` | `false` | **Emergency only.** `1` restores the automatic AMQP→REST switch. That switch provisions the RETIRED bare queue, which re-creates its subscription and fans the topic out to two queues — reproduced on tenant-1 2026-08-11 across five topics in a 65-second two-instance window, leaving 3 stale consumer flows per queue. Default off: a failed attach stands the topic down and retries |

## Standing down (default) vs falling back (opt-in)

A failed AMQP attach no longer switches transport. The transport is a
configuration decision; the pod records why it is not consuming and retries
every 30 s.

| Reason | Healthy? | Retried? | Readiness under `REQUIRE_AMQP` |
|---|---|---|---|
| `peer_owns_queue` | **yes** — another instance holds the queue, the work is not lost | yes, so this pod takes over if that instance goes away | **200**, deliberately |
| `amqp_unreachable` | no | yes | 503 |
| `proton_missing` | no — image defect | **no**, an import does not start working | 503 |

`GET /api/v1/health/readiness` → `event_mesh_consume.stood_down` is
`{topic: reason}`. It is reported separately from `rest_fallback` on purpose:
the two send you to different procedures.

## Observability surfaces

- `GET /api/v1/health/readiness` → `event_mesh_consume: {configured, active, rest_fallback, cooldown_scope, active_cooldowns}` (+ 503 khi REQUIRE_AMQP và rest_fallback).
- `GET /api/v1/health/eventmesh` → broker `health_check()` giờ gồm `consume` snapshot.

## Procedures

### Pod on REST fallback (`rest_fallback: true`)
1. Check boot logs: `proton is not importable` (image missing libs) vs `AMQP unreachable` (endpoint/firewall/auth).
2. Verify the service key has the `amqp10ws` endpoint; `EVENT_MESH_AMQP_URL` overrides.
3. Fix + restart the pod; confirm `eventmesh.consume.transport.rest == 0` and log line `consume transport: AMQP push`.

### Sustained 429s (REST mode)
- Instance-wide budget is shared with publishes and other components — see the capacity work (SA-1516).
- Raise `EVENT_MESH_POLL_INTERVAL_S`, reduce queue count, or move to AMQP.

Refs: SA-1470 (parent SA-1302) · consumer: `app/layer4_frameworks/messaging/event_mesh_consumer.py` · related runbooks: `event-mesh-dlq.md` (SA-1471), `response-topic-quota.md` (SA-1466).
