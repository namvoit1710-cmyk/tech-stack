"""Metrics wrapper for EventMeshBrokerClient.

All calls are safe when ``monitor`` is None — the broker client can be
constructed without a monitor in tests or bootstrap phases where metrics
aren't wired yet.
"""

from __future__ import annotations

from typing import Any

from app.layer2_application.interfaces.monitor_interface import IMonitor


class EventMeshMetrics:
    """Thin wrapper translating Event Mesh events into IMonitor calls."""

    def __init__(self, monitor: IMonitor | None = None) -> None:
        self._monitor = monitor

    def _increment(
        self, name: str, tags: dict | None = None, amount: int = 1,
    ) -> None:
        if self._monitor is None:
            return
        try:
            self._monitor.increment(name, tags=tags or {}, amount=amount)
        except TypeError:
            # IMonitor is a Protocol, so a monitor predating `amount` is
            # possible. Without this branch the blanket `except` below would
            # swallow the TypeError and every Event Mesh metric would vanish
            # silently — the exact failure shape this module exists to catch.
            try:
                for _ in range(max(1, amount)):
                    self._monitor.increment(name, tags=tags or {})
            except Exception:
                pass
        except Exception:
            pass  # monitor failures must never break the publish path

    def _observe(self, name: str, value: float) -> None:
        if self._monitor is None:
            return
        try:
            self._monitor.histogram(name, value, tags={})
        except Exception:
            pass  # monitor failures must never break the publish path

    def _track(self, name: str, value: float) -> None:
        if self._monitor is None:
            return
        try:
            self._monitor.track(name, value)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Publish lifecycle
    # ------------------------------------------------------------------

    def record_publish_success(self, topic: str, latency_ms: float, attempts: int) -> None:
        self._increment("eventmesh.publish.success", {"topic": topic, "attempts": str(attempts)})
        self._track("eventmesh.publish.latency_ms", latency_ms)

    def record_publish_failure(self, topic: str, reason: str, attempts: int) -> None:
        self._increment(
            "eventmesh.publish.failure",
            {"topic": topic, "reason": reason, "attempts": str(attempts)},
        )

    def record_publish_retry(self, topic: str, attempt: int, reason: str) -> None:
        self._increment(
            "eventmesh.publish.retry",
            {"topic": topic, "attempt": str(attempt), "reason": reason},
        )

    def record_dropped(self, reason: str, topic: str = "") -> None:
        self._increment("eventmesh.publish.dropped", {"reason": reason, "topic": topic})

    # ------------------------------------------------------------------
    # DLQ durability + circuit alerting (SA-1471)
    # ------------------------------------------------------------------

    def record_circuit_state(self, state: str) -> None:
        """Gauge: 1.0 while the publish circuit is OPEN/HALF_OPEN, 0.0 when
        CLOSED. Alert rule pages on sustained 1.0 — see
        docs/runbooks/event-mesh-dlq.md."""
        self._track("eventmesh.circuit.open", 0.0 if state == "CLOSED" else 1.0)

    def record_dlq_fallback(self, reason: str, topic: str = "") -> None:
        """A durable envelope was parked in the LOCAL spool instead of being
        dropped (DLQ disabled / unreachable). Not a loss — pages so ops know
        durability is running on the fallback."""
        self._increment("eventmesh.dlq.fallback", {"reason": reason, "topic": topic})

    def record_flush_shape(
        self, *, groups: int, largest: int, width: int, batch: int,
    ) -> None:
        """Shape of one flush batch: destinations, biggest slice, width used.

        The dispatch width is derived per batch rather than configured, so
        these are the inputs that explain it. When someone adds an event type
        the makespan moves and ``largest`` is what says why — without it the
        symptom is "runs got slower" with no cause attached.

        HISTOGRAMS, not gauges. This started as four gauges and they were
        useless: sampled after a probe finished they all read 1.0, which looks
        exactly like "batches are size 1" and is really "the last flush while
        idle was one message". A gauge read at idle only ever tells you about
        idle, and that ambiguity cost a live A/B (F13). A histogram keeps the
        whole distribution, so the shape during a run survives to scrape time.

        All four are recorded together so the set is never partial.
        """
        self._observe("eventmesh.flush.groups", groups)
        self._observe("eventmesh.flush.largest_group", largest)
        self._observe("eventmesh.flush.width", width)
        self._observe("eventmesh.flush.batch_size", batch)

    def record_spool_depth(self, depth: int) -> None:
        self._track("eventmesh.dlq.spool.depth", depth)

    def record_spool_replayed(self, topic: str = "") -> None:
        self._increment("eventmesh.dlq.spool.replayed", {"topic": topic})

    # ------------------------------------------------------------------
    # Consume transport + 429 isolation (SA-1470)
    # ------------------------------------------------------------------

    def record_consume_transport(self, active: str) -> None:
        """Gauge: 1.0 while consumption runs over the REST poll loop, 0.0 on
        AMQP push. Alert on sustained 1.0 when the configured transport is
        amqp — see docs/runbooks/event-mesh-consume-transport.md."""
        self._track("eventmesh.consume.transport.rest", 1.0 if active == "rest" else 0.0)

    def record_consume_fallback(self, reason: str) -> None:
        """The consumer was configured for AMQP push but committed to the
        REST poll loop for the process lifetime (silent until SA-1470)."""
        self._increment("eventmesh.consume.fallback", {"reason": reason})

    def record_consume_standdown(self, queue: str, reason: str) -> None:
        """This pod is deliberately not consuming a topic.

        Distinct from ``consume.fallback``: that one says "switched transport",
        this one says "consuming nothing here, on purpose". ``reason=peer_owns_queue``
        is healthy (another instance has the queue); the other reasons are not.

        Both labels are ALWAYS supplied — a Prometheus label set is frozen on
        first use and a later sample with different keys is dropped silently.
        """
        self._increment(
            "eventmesh.consume.standdown", {"queue": queue, "reason": reason},
        )

    def record_consume_rate_limited(
        self, queue: str, cooldown_s: float, streak: int, scope: str,
    ) -> None:
        """A 429 on the consumption endpoint engaged a cooldown."""
        self._increment(
            "eventmesh.consume.rate_limited",
            {"queue": queue, "streak": str(streak), "scope": scope},
        )
        self._track("eventmesh.consume.cooldown_seconds", cooldown_s)

    # ------------------------------------------------------------------
    # AMQP push liveness (G2/P4)
    # ------------------------------------------------------------------

    def record_amqp_delivery(self, queue: str, count: int = 1) -> None:
        """Messages a receiver link actually delivered. The positive signal:
        a link with an attached gauge of 1 and no deliveries is the failure
        mode that took tenant-1 down three times."""
        if count > 0:
            # `count` is the AMOUNT, never a label. As a label the counter reads
            # 1 and every distinct delta spawns a new series — measured on
            # tenant-1 2026-08-05: six queues, 19 series in five minutes, and
            # `rate(eventmesh.amqp.delivery)` (the positive health signal this
            # metric exists for, per the runbook) reduced to noise.
            self._increment("eventmesh.amqp.delivery", {"queue": queue}, amount=count)

    def record_amqp_stall(self, queue: str) -> None:
        """A link was attached, the queue had messages waiting, and nothing
        was delivered for the stall window — so the watchdog stood it down.

        This IS the alert. The condition ("attached AND depth > 0 AND
        deliveries flat") is evaluated in the consumer, where all three facts
        are already in hand, rather than reconstructed from three separate
        series in a query language. See
        docs/runbooks/event-mesh-amqp-liveness.md."""
        self._increment("eventmesh.amqp.stall", {"queue": queue})

    def record_amqp_reconnect(self, queue: str, reason: str) -> None:
        """A receiver link was re-established. ``reason`` separates a routine
        token rotation from a watchdog stand-down — a rising watchdog_stall
        rate means the broker keeps handing us dead links."""
        self._increment("eventmesh.amqp.reconnect", {"queue": queue, "reason": reason})

    def record_amqp_links(self, attached: int, stalled: int) -> None:
        """Gauges: receiver links currently attached, and how many of those
        are in the stall window. Page on sustained stalled > 0."""
        self._track("eventmesh.amqp.links.attached", float(attached))
        self._track("eventmesh.amqp.links.stalled", float(stalled))

    # ------------------------------------------------------------------
    # Dead letters
    # ------------------------------------------------------------------

    def record_dead_lettered(self, dmq: str, count: int) -> None:
        """Messages newly dead-lettered on *dmq* since the last sample.

        **This is the loss alert.** Every other Event Mesh signal describes the
        transport — links, stalls, reconnects, deliveries — and all of them read
        healthy while messages are being dropped, because the messages really
        were delivered before something threw them away. A DMQ growing is the
        only signal that says work is being lost.

        A counter, not a gauge, and labelled by queue: depth stays high forever
        after one incident, so only a rate can distinguish "losing messages now"
        from "lost some last week".
        """
        if count > 0:
            self._increment("eventmesh.dmq.deadlettered", {"queue": dmq}, amount=count)

    def record_dmq_depth(self, total: int) -> None:
        """Gauge: messages sitting across every dead-message queue.

        Aggregate because ``IMonitor.track`` has no labels. It answers the one
        question the counter cannot — *is anything dead-lettered right now* —
        including a backlog that predates this process, which no delta can see.
        """
        self._track("eventmesh.dmq.depth", float(total))

    # ------------------------------------------------------------------
    # Token lifecycle
    # ------------------------------------------------------------------

    def record_token_refresh(self, outcome: str) -> None:
        self._increment("eventmesh.token.refresh", {"outcome": outcome})

    # ------------------------------------------------------------------
    # Buffer (reserved for Phase 2)
    # ------------------------------------------------------------------

    def record_buffer_depth(self, depth: int) -> None:
        self._track("eventmesh.buffer.depth", depth)

    def record_buffer_full(self, topic: str = "") -> None:
        """The bounded send buffer rejected an enqueue (SA-1469). Durable
        events overflow to the spool (not a loss); best-effort events drop
        counted. Alert on any sustained increase — see runbook."""
        self._increment("eventmesh.buffer.full", {"topic": topic})
