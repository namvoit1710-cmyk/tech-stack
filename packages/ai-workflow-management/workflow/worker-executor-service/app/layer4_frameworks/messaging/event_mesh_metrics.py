"""Metrics wrapper for the executor's Event Mesh consumption.

Task intake had no instrumentation at all: whether the executor was receiving
work could only be inferred from the absence of task logs, which is exactly how
the control plane's three AMQP outages stayed invisible for so long.

Every call is safe when ``monitor`` is None, and safe against a monitor that
predates these methods — the broker client can be constructed in tests or
bootstrap phases where metrics aren't wired. Reporting must never be the thing
that breaks consumption.

The method names are deliberately NOT the ``IMonitor.increment(name)`` ad-hoc
path: that maps to ``wes_events_total{event=...}`` with no queue label, and
``IMonitor.track`` routes by substring match on the metric name ("duration",
"active", "pending") so any other gauge is silently dropped. A metric that
silently does nothing is worse than no metric — it reads as covered.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class EventMeshMetrics:
    """Thin, failure-proof wrapper translating Event Mesh events into gauges."""

    def __init__(self, monitor: Any = None) -> None:
        self._monitor = monitor

    def _call(self, method: str, *args: Any) -> None:
        if self._monitor is None:
            return
        fn = getattr(self._monitor, method, None)
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:
            logger.debug("Event Mesh metric %s failed", method, exc_info=True)

    # ------------------------------------------------------------------
    # AMQP push liveness
    # ------------------------------------------------------------------

    def record_amqp_delivery(self, queue: str, count: int = 1) -> None:
        """Messages a receiver link actually delivered — the positive signal.

        A link with an attached gauge of 1 and no deliveries is the failure
        mode that took the control plane down three times.
        """
        self._call("eventmesh_amqp_delivery", queue, count)

    def record_amqp_stall(self, queue: str) -> None:
        """A link was attached, the queue had messages waiting, and nothing was
        delivered for the stall window — so the watchdog stood it down.

        This IS the alert. The condition (attached AND depth > 0 AND deliveries
        flat) is evaluated in the consumer, where all three facts are already in
        hand, rather than reassembled from three series in a query language.
        """
        self._call("eventmesh_amqp_stall", queue)

    def record_amqp_reconnect(self, queue: str, reason: str) -> None:
        """A receiver link was re-established. ``reason`` separates a routine
        token rotation from a watchdog stand-down — a rising ``disconnect``
        rate means the broker keeps handing us links that die."""
        self._call("eventmesh_amqp_reconnect", queue, reason)

    def record_amqp_links(self, attached: int, stalled: int) -> None:
        self._call("eventmesh_amqp_links", attached, stalled)

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------

    def record_token_refresh(self, outcome: str) -> None:
        """The OAuth2 token was re-minted. ``outcome`` is success | failure.

        Until 2026-08-16 the executor had no token metric at all, which is why
        a 40,327-reconnect-per-week storm driven entirely by token rotation was
        indistinguishable, from outside the process, from a flaky broker. Pair
        it with ``eventmesh_amqp_reconnect{reason="token_expiry"}``: a healthy
        service shows roughly one reconnect per queue per refresh. Reconnects
        far outnumbering refreshes means links are being rebuilt on a token
        that never actually changed.
        """
        self._call("eventmesh_token_refresh", outcome)

    # ------------------------------------------------------------------
    # Transport selection
    # ------------------------------------------------------------------

    def record_consume_transport(self, active: str) -> None:
        self._call("eventmesh_consume_transport", active)

    def record_consume_fallback(self, reason: str) -> None:
        """Configured for AMQP but committed to REST for the process lifetime.

        Silent until now: the fallback is deliberately transparent so intake
        never stops, which also meant nobody could tell AMQP had never engaged.
        """
        self._call("eventmesh_consume_fallback", reason)

    # ------------------------------------------------------------------
    # Dead letters
    # ------------------------------------------------------------------

    def record_dead_lettered(self, dmq: str, count: int) -> None:
        """Messages newly dead-lettered on *dmq* since the last sample.

        **This is the loss alert.** Every other signal here describes the
        transport — links, stalls, reconnects, deliveries — and all of them
        read healthy while tasks are being dropped, because the messages really
        were delivered before something threw them away.

        A counter, not a gauge, and labelled by queue: depth stays high forever
        after one incident, so only a rate can tell "losing tasks now" from
        "lost some last week".
        """
        self._call("eventmesh_dead_lettered", dmq, count)

    def record_dmq_depth(self, total: int) -> None:
        """Gauge: messages sitting across every dead-message queue.

        Aggregate, because it answers the one question the counter cannot —
        *is anything dead-lettered right now* — including a backlog that
        predates this process, which no delta can see.
        """
        self._call("eventmesh_dmq_depth", total)
