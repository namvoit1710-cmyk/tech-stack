# Runbook — AMQP receiver link attached but not delivering

## What this catches

On 2026-08-05, enabling `EVENT_MESH_CONSUME_TRANSPORT=amqp` on tenant-1 took the
control plane down three times. Each time the log showed **six receiver links OPEN and
then nothing** — no errors, no detaches, no disconnects — while every run hung with all
nodes `NOT_STARTED`. `_receive_loop` only reconnects when `run()` returns, and a link
that is attached-but-silent never returns, so the loop consumed nothing forever.

Nothing in the system could see it. These metrics exist so that never repeats.

## Signals

| metric | kind | meaning |
|---|---|---|
| `eventmesh.amqp.links.attached` | gauge | receiver links currently attached |
| `eventmesh.amqp.links.stalled` | gauge | of those, how many are inside the stall window |
| `eventmesh.amqp.stall` | counter `{queue}` | the watchdog stood a link down |
| `eventmesh.amqp.reconnect` | counter `{queue, reason}` | `token_expiry` \| `watchdog_stall` \| `disconnect` |
| `eventmesh.amqp.delivery` | counter `{queue}` | messages actually delivered |
| `eventmesh.consume.transport.rest` | gauge | 1.0 while consumption is on the REST poll loop |
| `eventmesh.consume.fallback` | counter `{reason}` | includes `queue_has_consumers` (G1) |
| `eventmesh.dmq.deadlettered` | counter `{queue}` | messages newly dead-lettered — **work is being lost** |
| `eventmesh.dmq.depth` | gauge | total sitting across every DMQ, including a backlog older than this process |

**Every signal above `eventmesh.dmq.*` describes the transport, and all of them read
healthy while messages are being destroyed** — the messages really were delivered, then
thrown away. `eventmesh.dmq.deadlettered` is the only one that says work is being lost.
It runs under **both** transports (it lives in the consumer, not the AMQP watchdog),
because REST is the rollback target and that is exactly when you need it.

Its first reading of each DMQ only seeds a baseline — otherwise every deploy would alarm on
messages lost weeks ago, and an alert that fires on every restart gets muted. A pre-existing
backlog is logged at startup instead.

**The alert is `eventmesh.amqp.stall`.** The condition — *attached* AND *queue depth > 0*
AND *deliveries flat* — is evaluated in the consumer, where all three facts are already
in hand, rather than reassembled from three series in a query language. A stall requires
**both** halves: a deep queue alone is not a stall (the handler may be slow) and a quiet
queue is not a stall either (that is an idle system).

## Page on

- `eventmesh.amqp.links.stalled > 0` sustained for 5 minutes
- `rate(eventmesh.amqp.stall)` rising on the same queue — the broker keeps handing us
  dead links; reconnecting is not fixing it
- **`rate(eventmesh.amqp.reconnect{reason="disconnect"}) > 1/min` on any queue** — a link
  that keeps dying and reconnecting. **A flapping link is not a stalled link**, so the
  watchdog will *not* fire and `links.stalled` stays 0: the link really is attached and
  really is delivering, right up until it dies. Measured on tenant-1 2026-08-05: 31
  reconnects in ~7 minutes went unalarmed while runs still completed, because the churn
  only hit the large-payload queues. Cost of missing it is duplicate processing (every
  reconnect redelivers whatever was unsettled), and a queue whose payloads always exceed
  the frame size never drains at all.
- `eventmesh.consume.fallback{reason="queue_has_consumers"}` — G1 refused to attach
  because something else is bound to that queue (see below)
- **`rate(eventmesh.dmq.deadlettered) > 0` on any queue** — messages are being lost right
  now. Page regardless of how healthy the transport looks, because it will look healthy:
  every link metric stayed green through the 2026-08-05 losses. Note a **flapping link
  dead-letters good messages** — each reconnect redelivers whatever was unsettled, so more
  than `maxRedeliveryCount` reconnects exhausts the budget on messages that were never
  poisonous. Correlate with `eventmesh.amqp.reconnect` before blaming the payload.

## Triage

**1. Is it one queue or all of them?**
One queue is the tenant-1 signature: `sub-orchestration-actions` delivered nothing while
`sub-workflow-triggers` was fine. All queues at once is more likely the endpoint or the
token.

**2. Check who else is bound.**
```
GET /hub/rest/api/v1/management/messaging/queues/<url-encoded-queue>
```
`consumerCount` is a **live** count, not a configured limit (verified: a queue nothing has
ever consumed from reads 0). Expect **1** — just our link.

- **3 or 4** ⇒ REST consumer flows are still bound. SAP allocates 3 per REST-polled queue
  and only releases them after **30 minutes with no publishing**, which a hot queue never
  has. The queues are `NON_EXCLUSIVE`, so the broker round-robins and whatever it hands a
  stopped flow is stranded (measured: 20 published, 17 delivered, 3 `unacked` forever).
  This is the original outage. It should now be impossible — the AMQP transport uses its
  own `<queue>-amqp` queue — so finding it means something is consuming the AMQP queue
  over REST. Look for a fallback that landed on the wrong queue.
- **0** ⇒ the link is not actually attached; this is a connectivity problem, not a stall.

**2b. Is the link flapping rather than stalling?**
Look for `AMQP receiver (queue=…) ended after <n>s (attached=True): …` in the log.

Repeated `AMQP FIRST delivery` lines for the same queue are the tell — each one means a
**fresh** bridge, so the previous one died. **No** `FIRST delivery` at all, paired with
`ended after 0.1s` on a 1/2/4/8/16/30 s backoff, means the link is dying on its first
inbound frame and has never delivered anything.

If the reason is `unable to process all bytes: <consumed>, <offered>` — proton consumed the
first number and we handed it the second — the frame exceeded proton's **input buffer**,
not necessarily the negotiated frame size. Two different limits:

| limit | bounds | knob |
|---|---|---|
| `max_frame_size` | what the peer may send in one frame | `EVENT_MESH_AMQP_MAX_FRAME_BYTES` (default 1 MiB; must exceed the largest payload — the publisher caps at 900 KB) |
| `transport.capacity()` | what we may hand proton in one `push` | none — `_feed` chunks to it automatically |

Since `solace-971` the second is handled in code, so this line should not reappear. If it
does, the peer is sending frames larger than the buffer can ever assemble; `_feed` fails
the link with `AMQP input buffer stuck at 0 capacity` rather than desynchronise the stream.
Raise `EVENT_MESH_AMQP_MAX_FRAME_BYTES` in that case.

Do not read a rising capacity as progress: raising the frame size alone moved it 99 → 7973
bytes on tenant-1 and the 14039-byte Jira payloads still killed the link.

**3. Check the DMQ.** `<queue>-dmq` filling up means messages are failing the handler and
exhausting `maxRedeliveryCount`, not that delivery is stuck.

## Mitigation

Roll back to REST — it is one env var and it has always drained the backlog cleanly:

```
cf set-env <app> EVENT_MESH_CONSUME_TRANSPORT rest
cf restart <app>
```

No data is lost: messages stay spooled and the REST poll loop picks them up. All three
tenant-1 rollbacks drained to COMPLETED.

**Do not delete and re-provision the queue.** Every message published while the topic has
no subscribed queue is discarded silently and the publisher still gets HTTP 204 (measured:
5 published into the gap, 0 arrived).

## Tunables

| env | default | notes |
|---|---|---|
| `EVENT_MESH_AMQP_STALL_CHECK_S` | 30 | how often the watchdog looks |
| `EVENT_MESH_AMQP_STALL_AFTER_S` | 90 | silence + waiting messages before standing a link down |
| `EVENT_MESH_AMQP_TOKEN_MARGIN_S` | 300 | reconnect this long before the 12 h token expires |
| `EVENT_MESH_AMQP_PREFETCH` | 10 | messages held unacked per link |

Repeated forced reconnects widen the stall window geometrically (up to 8×), so a queue
that genuinely cannot deliver does not reconnect-storm the broker.

## The watchdog watches LINKS, not queues

`concurrency` opens that many AMQP receiver links onto the **same** queue. Two things
follow, and both were wrong until the per-link fix:

- **Every link is watched.** The bridge registry is keyed by `(queue, index)`. Keyed by
  queue, N links overwrote each other and only the last to register was ever checked — so
  N-1 could go silent unseen, which is exactly the failure this watchdog exists to catch,
  and `links_attached` reported 1 while N existed.
- **A reconnect resets that link's delivery baseline.** `deliveries` restarts at 0 on every
  new bridge. Compared against the previous bridge's lifetime total it reads as "no
  progress" forever, so on a queue with work waiting the watchdog would stand down a
  healthy, actively-delivering link — and its replacement would start from 0 again. That
  loop also flatlines `amqp_delivery_total`, i.e. the watchdog manufactures the exact
  symptoms it exists to detect. **It needed no concurrency: a 12-hour token rotation was
  enough.** The forced-reconnect streak deliberately survives the reset, so a queue that
  genuinely cannot deliver still widens its window instead of reconnect-storming.

Queue depth is read once per queue per pass, not once per link, so raising `concurrency`
does not multiply management-API calls.
