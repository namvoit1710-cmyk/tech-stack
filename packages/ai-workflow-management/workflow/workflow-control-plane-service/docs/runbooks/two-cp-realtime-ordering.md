# Runbook — per-run event ordering with 2+ control-plane instances (SA-1518)

With more than one WCP instance, `sub-realtime-events` (and `sub-workflow-results`)
are **competing-consumer** queues: every envelope is delivered to exactly one
instance. Each instance resequences per run via `_run_seq` + a reorder buffer
(`socketio_consumer._RunOrderState`), force-flushing gaps after
`REALTIME_GAP_FLUSH_SECONDS` (default 3s).

## Proven guarantees (CI: `tests/unit/test_two_cp_realtime_ordering.py`)

| # | Guarantee | Mechanism |
|---|---|---|
| 1 | **Zero loss** — the union of all instances' emissions covers every `_run_seq`; gap-flush and the late path EMIT, never drop | `_flush_gaps` + late-emit branch |
| 2 | **Bounded stall** — a peer-consumed or missing predecessor delays a buffered event at most one gap window; a run never freezes | gap window timer |
| 3 | **Within-instance order** — each instance emits its subset in ascending seq order | reorder buffer + seq-sorted flush |
| 4 | **Duplicates surface** (at-least-once redelivery) — emitted late, never silently dropped | late-emit branch |

## Documented limits (NOT guaranteed)

- **Cross-instance total order for one client.** A client receives each event from
  whichever instance consumed it; between instances there is no global order token
  beyond `_run_seq` itself. Consumers/frontends must treat `_run_seq` as the
  authoritative order (sort/patch by seq), NOT arrival order.
- **Duplicates are delivered** when they arrive after their slot passed (late
  emit); a duplicate redelivered while its seq is still buffered coalesces to a
  single emission (dict slot overwrite — no loss). Dedupe belongs to the
  idempotency projector (SA-1375). UI reducers must be idempotent per
  `(run_key, _run_seq)`.
- If strict single-stream delivery per run ever becomes a requirement, adopt
  sticky per-run routing (key-based queue partitioning — see SA-1515 ADR) instead
  of competing consumption.

## Ops notes

- Tune `REALTIME_GAP_FLUSH_SECONDS` ↑ if logs show frequent
  `realtime: gap flush … missing run_seq` with events arriving just after
  (Event Mesh tail latency), ↓ for snappier multi-instance UI at the cost of
  more out-of-order late emits.
- `realtime: late event … emitting out of order` at high rate with 2+ instances
  is EXPECTED for peer-consumed seqs — alert only on growth of realtime queue
  drops (`Realtime queue full`), which are real losses.

Refs: SA-1518 (parent SA-1302) · relates SA-1469 (durable taxonomy), SA-1375 (dedupe), SA-1515 (topology ADR).
