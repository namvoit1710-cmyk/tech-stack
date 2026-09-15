# Runbook — Event Mesh DLQ durability + circuit alerting (SA-1471)

When SAP Event Mesh degrades, terminally-failed publishes flow:
`retries exhausted / circuit OPEN` → `publisher.dlq` queue → **local spool**
(when the DLQ itself can't take them). Nothing durable is silently dropped.

## Metrics (via IMonitor)

| Metric | Type | Meaning |
|---|---|---|
| `eventmesh.circuit.open` | gauge | 1.0 while the publish circuit is OPEN/HALF_OPEN, 0.0 when CLOSED |
| `eventmesh.circuit.state_change` | counter | transitions, tagged `from`/`to` |
| `eventmesh.publish.dlq` | counter | envelopes parked on `publisher.dlq` |
| `eventmesh.dlq.fallback` | counter | durable envelopes parked in the LOCAL spool (`reason` = dlq_disabled / dlq_no_client / dlq_failed) — **not a loss** |
| `eventmesh.dlq.spool.depth` | gauge | envelopes currently parked locally |
| `eventmesh.dlq.spool.replayed` | counter | envelopes re-enqueued from the spool |
| `eventmesh.publish.dropped{reason=dlq_*}` | counter | **true losses** (spool disabled/full/unwritable) |

## Alert rules (page = wake a human)

1. **PAGE** — `eventmesh.circuit.open == 1` for > 2 minutes: broker incident in progress; durable events are accumulating in DLQ/spool.
2. **PAGE** — `increase(eventmesh.publish.dropped{reason=~"dlq_.*"}) > 0`: a durable event was actually lost. Should be ~impossible with the spool on — investigate immediately.
3. **WARN** — `eventmesh.dlq.fallback > 0`: durability is running on the local fallback; check why `publisher.dlq` is unreachable (broker down? DLQ disabled by config?).
4. **WARN** — `eventmesh.dlq.spool.depth > 0` sustained after `eventmesh.circuit.open` returns to 0: replay didn't drain; see procedures.

## Config

| Env | Default | Notes |
|---|---|---|
| `EVENT_MESH_DLQ_ENABLED` | `1` | `publisher.dlq` parking. For durable events "disabled" now means "spool instead", not "drop" |
| `EVENT_MESH_DLQ_SPOOL_DIR` | `<tempdir>/eventmesh-dlq-spool` | empty ⇒ spool off (legacy drop behaviour). Container-local: narrows the loss window to pod-restart-during-outage; it is not a WAL |
| `EVENT_MESH_DLQ_BEST_EFFORT_TOPICS` | `realtime.events` | comma-separated topics allowed to drop (counted) instead of spooling |

## Procedures

### After a broker incident
1. Confirm `eventmesh.circuit.open` returned to 0 (circuit CLOSED — the pump logs `DLQ spool replay (circuit_closed): re-enqueued N`).
2. Spool drains **automatically** on circuit close and at startup. Verify `eventmesh.dlq.spool.depth == 0`.
3. Drain the parked `publisher.dlq` queue (not automatic — envelopes there predate the spool or arrived while the broker was flapping):
   ```bash
   python tools/replay_publisher_dlq.py --dry-run     # peek
   python tools/replay_publisher_dlq.py --yes         # replay + ack
   ```
4. Reconcile: projections/audit heal as replayed events land (consumers are idempotent per run/task keys; `_run_seq` lets the UI resequence).

### Spool depth stuck > 0
- Pod alive but circuit never closed → broker still unhealthy; fix broker first.
- `health_check()["dlq_spool"]` (exposed via the publisher health snapshot) shows enabled/depth per pod.
- Manual last resort: the spool file is `$EVENT_MESH_DLQ_SPOOL_DIR/eventmesh-dlq-spool.jsonl` (one DLQ envelope per line, same schema as `publisher.dlq`).

## Publish backpressure (SA-1469)

A full send buffer (default 10,000) no longer silently discards durable domain
events: they overflow into the same local spool and the flusher replays them
(original order) once the buffer drains below 50%. Best-effort topics
(`EVENT_MESH_DLQ_BEST_EFFORT_TOPICS`, default `realtime.events`) may still
drop — counted, never silent.

| Metric | Meaning |
|---|---|
| `eventmesh.buffer.full` | an enqueue was rejected (durable → spooled; best-effort → dropped counted) |
| `eventmesh.buffer.depth` | live buffer depth — alert at >80% of `EVENT_MESH_BUFFER_SIZE` (high-water) |

Alert rules: **PAGE** on `increase(eventmesh.publish.dropped{reason="buffer_full"}) > 0`
(true loss — spool off/full); **WARN** on sustained `eventmesh.buffer.full` growth or
buffer depth >80% (fan-out exceeding drain rate — raise buffer, or shard, see SA-1516).

Refs: SA-1471 + SA-1469 (parent SA-1302) · pump: `app/layer4_frameworks/messaging/event_mesh_publisher_pump.py` · spool: `event_mesh_dlq_spool.py` · related: SA-1469 (backpressure), SA-1466 (`response-topic-quota.md` — same alert-rail pattern).
