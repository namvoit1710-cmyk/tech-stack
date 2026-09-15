"""Push Gateway consumer subscribed to the realtime broker topic.

Consumes the same ``realtime.events`` envelope that ``SocketIORealtimeConsumer``
reads, but forwards each room key to the Push Gateway via a pluggable client.
The transport protocol (HTTP, gRPC, …) is selected through the
``PushGatewayClientRegistry`` — see ``push_gateway/`` sub-package.

**Per-run resequencing.** Like ``SocketIORealtimeConsumer``, this consumer
resequences per run *before* forwarding. Each envelope carries a contiguous
``_run_seq`` stamped by ``RealtimeBroadcastingPublisher``; a small reorder
buffer (``_RunOrderState``) keyed by ``_run_key`` holds a future event until its
predecessor arrives, so the Push Gateway — and therefore the frontend that
consumes it — sees events in publish order even when Event Mesh delivers them
out of order (HTTP-polled queues + lock-TTL redelivery).

This closes a real bug: previously this consumer had NO reorder buffer and only
stamped ``_run_seq`` into the payload, delegating resequencing to "gateway
subscribers" that never implemented it. On Event Mesh a ``run.status=completed``
could overtake the final ``node.completed``; the frontend, which treats the
terminal run event as end-of-stream, then dropped the trailing node status and
left the node stuck "executing". Resequencing here guarantees the terminal run
event is forwarded only after every lower-seq node event. If a predecessor never
arrives within ``_GAP_FLUSH_SECONDS`` the buffer force-flushes so a run never
stalls.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from app.layer2_application.interfaces.broker_client_interface import IBrokerClient
from app.layer4_frameworks.realtime.push_gateway.push_gateway_registry import IPushGatewayClient

logger = logging.getLogger(__name__)

REALTIME_TOPIC = "realtime.events"

# Resequencing knobs — shared with ``SocketIORealtimeConsumer`` via the same env
# vars so both realtime sinks behave identically under out-of-order delivery.
_GAP_FLUSH_SECONDS = float(os.getenv("REALTIME_GAP_FLUSH_SECONDS", "3.0"))
# Idle per-run reorder state older than this is evicted to bound memory.
_ORDER_STATE_TTL_SECONDS = 300.0
# Drain budget per round so a flood doesn't monopolise the event loop.
_DRAIN_BUDGET = int(os.getenv("REALTIME_DRAIN_BUDGET", "200"))


class _RunOrderState:
    """Reorder buffer for a single run's realtime event stream."""

    __slots__ = ("next_seq", "buffered", "last_activity", "cold_started")

    def __init__(self, next_seq: int = 1, cold_started: bool = True) -> None:
        # Next ``_run_seq`` expected to be forwarded. Cold-join starts at 1 and
        # lets ``_flush_gaps`` advance silently past earlier seqs that were
        # forwarded before this consumer subscribed (see ``SocketIORealtimeConsumer``
        # for the full rationale).
        self.next_seq = next_seq
        # seq -> (message, arrival_monotonic)
        self.buffered: dict[int, tuple[dict, float]] = {}
        self.last_activity = time.monotonic()
        self.cold_started = cold_started


class PushGatewayRealtimeConsumer:
    """Forwards realtime broker events to Push Gateway via pluggable client.

    Single bounded FIFO queue with per-run resequencing before forwarding.
    """

    def __init__(
        self,
        client: IPushGatewayClient,
        broker: IBrokerClient | None = None,
    ) -> None:
        self._client = client
        self._broker = broker
        # SA-2265 — was a hard-coded 10 000 while socketio_consumer used a
        # different, env-tunable 16 000. Same class of buffer, two unrelated
        # numbers, both sized for a 1-2GB pod. One derivation now feeds both.
        from app.layer4_frameworks.realtime.queue_budget import (
            RealtimeQueueStats,
            realtime_queue_maxsize,
        )

        maxsize = realtime_queue_maxsize()
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=maxsize)
        self.queue_stats = RealtimeQueueStats(
            "realtime_push_gateway", self._queue, maxsize,
        )
        try:
            from app.layer2_application.interfaces.cache_budget_registry import (
                default_cache_budget_registry,
            )
            default_cache_budget_registry().register(
                self.queue_stats, "realtime_push_gateway",
            )
        except Exception:  # pragma: no cover — observability must never block boot
            logger.debug("push gateway queue stats not registered", exc_info=True)
        self._loop: asyncio.AbstractEventLoop | None = None
        # Wake-up signal so the drain loop can also run ``_flush_gaps`` on a
        # timer even while the queue is idle.
        self._has_messages = asyncio.Event()
        # Per-run reorder buffers, keyed by envelope ``_run_key``.
        self._order: dict[str, _RunOrderState] = {}

    def notify(self, message: dict[str, Any]) -> None:
        """Direct-notify path used when no realtime broker is configured."""
        if self._loop is None or self._loop.is_closed():
            return
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, message)
            self._loop.call_soon_threadsafe(self._has_messages.set)
            logger.debug(
                "[PUSH] ENQUEUE seq=%s event=%s qsize=%d path=direct",
                message.get("_seq", "?"),
                message.get("event_type", "?"),
                self._queue.qsize(),
            )
        except asyncio.QueueFull:
            self.queue_stats.record_drop()
            logger.warning(
                "[PUSH] QUEUE FULL seq=%s event=%s dropped_total=%d — dropping",
                message.get("_seq", "?"),
                message.get("event_type", "?"),
                self.queue_stats.dropped,
            )

    def start(self) -> None:
        """Subscribe to the broker topic and capture the event loop."""
        self._loop = asyncio.get_running_loop()

        if self._broker is None:
            logger.debug("No broker — Push Gateway consumer in direct-notify mode")
            return

        if hasattr(self._broker, "ensure_topic"):
            try:
                self._broker.ensure_topic(REALTIME_TOPIC)
            except Exception:
                logger.warning("Could not auto-create topic '%s'", REALTIME_TOPIC)

        def _on_message(message: dict[str, Any]) -> None:
            if self._loop is None or self._loop.is_closed():
                return
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, message)
                self._loop.call_soon_threadsafe(self._has_messages.set)
                logger.debug(
                    "[PUSH] ENQUEUE seq=%s event=%s qsize=%d path=broker",
                    message.get("_seq", "?"),
                    message.get("event_type", "?"),
                    self._queue.qsize(),
                )
            except asyncio.QueueFull:
                self.queue_stats.record_drop()
                logger.warning(
                    "[PUSH] QUEUE FULL seq=%s event=%s dropped_total=%d — dropping",
                    message.get("_seq", "?"),
                    message.get("event_type", "?"),
                    self.queue_stats.dropped,
                )

        self._broker.subscribe_to_topic(REALTIME_TOPIC, _on_message)
        logger.debug(
            "Push Gateway consumer subscribed to '%s' (provider=%s)",
            REALTIME_TOPIC, self._client.provider_name,
        )

    async def run_forever(self) -> None:
        logger.debug(
            "Push Gateway realtime consumer started (provider=%s, single-queue "
            "FIFO + per-run resequencing)",
            self._client.provider_name,
        )
        while True:
            try:
                drained = await self._drain_round()
                if not drained:
                    # No messages — wait for a wake-up, but time out so
                    # ``_flush_gaps`` still fires for buffered-but-stalled runs.
                    self._has_messages.clear()
                    try:
                        await asyncio.wait_for(self._has_messages.wait(), timeout=0.1)
                    except asyncio.TimeoutError:
                        pass
            except Exception:
                logger.exception("Push Gateway consumer error")

    async def _drain_round(self) -> int:
        """Route up to ``_DRAIN_BUDGET`` queued messages through the reorder
        buffer, then force-flush any stale gaps."""
        drained = 0
        for _ in range(_DRAIN_BUDGET):
            if self._queue.empty():
                break
            try:
                msg = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            await self._accept(msg)
            drained += 1

        await self._flush_gaps()
        return drained

    async def _accept(self, message: dict[str, Any]) -> None:
        """Route a message through its run's reorder buffer before forwarding.

        Forwards immediately if it's the next expected event, buffers it if a
        predecessor is still missing, and forwards late (with a warning) if it
        arrives after its slot already passed.
        """
        run_key = message.get("_run_key")
        run_seq = message.get("_run_seq")
        if not run_key or not isinstance(run_seq, int) or run_seq <= 0:
            # No run context (e.g. workflow-scoped event) — nothing to
            # resequence against; forward as-is.
            await self._process(message)
            return

        state = self._order.get(run_key)
        if state is None:
            # Cold join: anchor at seq 1, not at whatever we observe first, so
            # broker-redelivered / queue-lagged earlier events get a window to
            # arrive before ``_flush_gaps`` advances past them.
            state = _RunOrderState(next_seq=1, cold_started=True)
            self._order[run_key] = state
        state.last_activity = time.monotonic()

        if run_seq == state.next_seq:
            await self._process(message)
            state.next_seq += 1
            # First in-order forward confirms we're synced with the publisher —
            # any later gap is real, not a cold join.
            state.cold_started = False
            await self._drain_buffered(state)
        elif run_seq > state.next_seq:
            # Future event — hold it until the gap fills.
            state.buffered[run_seq] = (message, time.monotonic())
        else:
            # Slot already passed (predecessor was gap-flushed, or a broker
            # redelivery). Forward late rather than drop — a missing event is
            # worse than a momentarily out-of-order one.
            logger.warning(
                "[PUSH] late event run_seq=%d for run=%s (next=%d) — forwarding out of order",
                run_seq, run_key, state.next_seq,
            )
            await self._process(message)

    async def _drain_buffered(self, state: _RunOrderState) -> None:
        """Forward any buffered events that have now become contiguous."""
        while state.next_seq in state.buffered:
            msg, _ = state.buffered.pop(state.next_seq)
            await self._process(msg)
            state.next_seq += 1

    async def _flush_gaps(self) -> None:
        """Force-flush stale reorder buffers and evict idle run state.

        Called once per drain round. If a buffered event has waited longer than
        ``_GAP_FLUSH_SECONDS`` for a missing predecessor, the gap is presumed
        permanent and everything buffered is flushed in seq order so a run never
        stalls. Cold-start vs genuine gap is distinguished by ``cold_started``.
        """
        now = time.monotonic()
        for run_key in list(self._order.keys()):
            state = self._order.get(run_key)
            if state is None:
                continue
            if not state.buffered:
                if now - state.last_activity > _ORDER_STATE_TTL_SECONDS:
                    del self._order[run_key]
                continue
            oldest = min(arrival for _, arrival in state.buffered.values())
            if now - oldest < _GAP_FLUSH_SECONDS:
                continue
            ordered = sorted(state.buffered)
            if state.cold_started:
                # First flush after cold join: advance silently to the lowest
                # buffered seq — "missing" earlier seqs were almost certainly
                # forwarded before this consumer subscribed.
                logger.debug(
                    "[PUSH] cold-join flush for run=%s — starting stream at "
                    "run_seq=%d (%d buffered)",
                    run_key, ordered[0], len(ordered),
                )
                state.next_seq = ordered[0]
                state.cold_started = False
            else:
                missing = [
                    s for s in range(state.next_seq, ordered[-1] + 1)
                    if s not in state.buffered
                ]
                logger.warning(
                    "[PUSH] gap flush for run=%s — missing run_seq %s, "
                    "flushing %d buffered event(s)",
                    run_key, missing, len(ordered),
                )
            for seq in ordered:
                msg, _ = state.buffered.pop(seq)
                await self._process(msg)
            state.next_seq = ordered[-1] + 1
            state.last_activity = now

    async def _process(self, message: dict[str, Any]) -> None:
        """Forward a single (already-resequenced) event to the Push Gateway."""
        event_type = message.get("event_type", "")
        rooms = message.get("rooms", [])
        payload = message.get("payload", {})
        seq = message.get("_seq", "?")
        if not event_type or not rooms:
            return

        # Stamp the per-run ordering keys (set by RealtimeBroadcastingPublisher
        # on the envelope) into the forwarded payload. Kept for downstream
        # debugging / defence-in-depth even though this consumer now resequences
        # before forwarding. Copy before mutating — the envelope and its payload
        # are shared with ``SocketIORealtimeConsumer``.
        run_seq = message.get("_run_seq")
        if isinstance(payload, dict) and isinstance(run_seq, int) and run_seq > 0:
            payload = {
                **payload,
                "_run_seq": run_seq,
                "_run_key": message.get("_run_key", ""),
            }

        published_at = message.get("_published_monotonic")
        queue_lag_ms = (
            (time.monotonic() - published_at) * 1000
            if isinstance(published_at, (int, float))
            else None
        )
        logger.debug(
            "[PUSH] DEQUEUE seq=%s event=%s rooms=%s queue_lag_ms=%s qsize=%d",
            seq, event_type, rooms,
            f"{queue_lag_ms:.1f}" if queue_lag_ms is not None else "n/a",
            self._queue.qsize(),
        )

        for room in rooms:
            t0 = time.monotonic()
            try:
                success = await self._client.send_event(
                    key=room,
                    event_type=event_type,
                    payload=payload,
                    is_final=False,
                )
                duration_ms = (time.monotonic() - t0) * 1000
                logger.debug(
                    "[PUSH] SENT seq=%s event=%s room=%s status=%s duration_ms=%.1f",
                    seq, event_type, room,
                    "ok" if success else "fail", duration_ms,
                )
            except Exception as exc:  # noqa: BLE001
                duration_ms = (time.monotonic() - t0) * 1000
                logger.warning(
                    "[PUSH] SENT seq=%s event=%s room=%s status=error duration_ms=%.1f err=%s",
                    seq, event_type, room, duration_ms, exc,
                )

    async def close(self) -> None:
        await self._client.close()
