"""Event-publishing primitives — extracted from OrchestrationEngine.

There are exactly two operations — *publish now* vs *collect for an
ordered drain*. The engine exposes them as ``publish_event`` /
``publish_events`` (direct) and ``emit`` / ``drain_events`` (buffered);
both pairs bottom out here.

This module is the single source of truth for both:

* **DIRECT** — call ``publish(topic, event)`` to push immediately onto
  the bound publisher chain. Use for events that trigger external
  systems (task dispatch, PENDING node requests) or terminal events
  (``run.completed``).
* **BUFFERED** — call ``buffer(topic, event)`` to stash into a
  per-async-task ``EventCollector``. ``await drain()`` flushes them
  in priority order (IMMEDIATE → NORMAL → TERMINAL). Use for
  lifecycle events emitted in the middle of an orchestration pass
  that should land in a predictable order on the wire.

The collector lives in a ``ContextVar`` so concurrent ``orchestrate_*``
calls each get their own buffer — buffered events from one run cannot
strand or interleave with another run's drain pass.
"""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any

from app.layer1_domain.events import topic_for_event
from app.layer2_application.events.event_collector import EventCollector


# Per-async-task event collector. Each ``orchestrate_*`` call gets its
# own collector via the contextvar so concurrent runs cannot corrupt
# each other. The collector is created lazily on the first ``buffer()``
# call and reset to ``None`` on ``drain()``.
_collector_var: contextvars.ContextVar[EventCollector | None] = (
    contextvars.ContextVar("event_collector", default=None)
)


class EventEmitter:
    """Owns the DIRECT-vs-BUFFERED publish choice.

    Constructed once at engine bootstrap with the chained publisher.
    The publisher is ``None``-safe — passing ``None`` (e.g. in tests
    that don't care about events) makes every publish a no-op.
    """

    def __init__(self, publisher: Any) -> None:
        self._publisher = publisher

    # ------------------------------------------------------------------
    # Direct publishing
    # ------------------------------------------------------------------

    def publish(self, topic: str, event: Any) -> None:
        """Publish a single event directly, bypassing the collector."""
        if self._publisher is not None:
            self._publisher.publish(topic, event)

    def publish_many(self, events: list) -> None:
        """Publish a list of events directly. Topic is resolved per event
        via :func:`topic_for_event` so callers don't need to pair each
        event with its topic constant."""
        if self._publisher is None:
            return
        for event in events:
            self._publisher.publish(topic_for_event(event), event)

    # ------------------------------------------------------------------
    # Buffered publishing (priority-ordered drain)
    # ------------------------------------------------------------------

    def buffer(self, topic: str, event: Any) -> None:
        """Stash an event into the per-async-task collector for the next
        :meth:`drain` boundary."""
        self._get_collector().append(topic, event)

    async def drain(self) -> None:
        """Publish every buffered envelope in priority order, then yield.

        Per-pass resolver-cache wiping was removed when materialisation
        + OData caches became run-scoped (see ``FileBackedResolverStrategy``)
        — they're now dropped on terminal transitions instead, which lets
        two handlers in the same run share one download instead of
        re-fetching on every drain boundary.

        The publish loop runs inline (no ``asyncio.to_thread``) so the
        realtime notifier's ``asyncio.get_running_loop()`` lookup
        succeeds — running in a worker thread would silently drop every
        event from the wire. Production publishers enqueue
        non-blockingly, so this doesn't stall the loop.
        """
        collector = _collector_var.get(None)
        if collector is None:
            return
        envelopes = collector.drain()
        _collector_var.set(None)  # reset for next pass
        if not envelopes or self._publisher is None:
            return
        for env in envelopes:
            self._publisher.publish(env.topic, env.event)
        await asyncio.sleep(0)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _get_collector() -> EventCollector:
        collector = _collector_var.get(None)
        if collector is None:
            collector = EventCollector()
            _collector_var.set(collector)
        return collector
