"""One bound for every realtime queue, derived from the pod's memory budget.

SA-2265. Two queues buffer the same class of thing — realtime event dicts on
their way to a browser — and they were sized independently and differently:
``socketio_consumer`` at 16 000 (env-tunable) and ``push_gateway_consumer``
hard-coded at 10 000. Neither number was derived from anything; both were
chosen when the pod had 1-2GB. At a conservative 2KB per event, 16 000 slots is
~32MB for a single queue — over a tenth of the entire 256MB target, spent on a
buffer, before any work is done.

How the bound is derived
------------------------
    maxsize = REALTIME_QUEUE_BUDGET_BYTES / REALTIME_EVENT_SIZE_ESTIMATE_BYTES
              / (number of queues sharing the budget)

Both inputs are explicit configuration rather than constants baked into the
formula, because both are ESTIMATES and P0-3 is what turns estimates into
measurements. The naming says so: ``..._ESTIMATE_BYTES``. When P0-3 reports the
real per-event size, one env var moves and both queues follow — which is the
actual point of unifying them.

Backpressure is DROP, and that is a decision, not an accident
-------------------------------------------------------------
Both consumers drop on overflow rather than blocking. Blocking would apply
backpressure to the event-mesh consumer thread, stalling delivery of events
that other parts of the system depend on for correctness (run state), in order
to protect a UI stream that is already best-effort and recovers by refetch. A
dropped realtime frame costs a stale panel until the next event; a blocked
consumer thread costs the run. Overflow is counted and exported so "the UI
looks stale" is diagnosable rather than mysterious.
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, "")
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        logger.warning("%s=%r is not an integer — using %d", name, raw, default)
        return max(minimum, default)


# Slice of the 256MB pod budget reserved for ALL realtime queues combined.
# 16MB ≈ 6% of the target: enough to ride out a burst, small enough that the
# buffer is not competing with the work it buffers for.
REALTIME_QUEUE_BUDGET_BYTES = _int_env("REALTIME_QUEUE_BUDGET_BYTES", 16 * 1024 * 1024)
# Per-event footprint ESTIMATE. A realtime envelope carries run/node identifiers
# and a status payload; 2KB is a deliberately pessimistic round number. P0-3
# replaces it with a measurement.
REALTIME_EVENT_SIZE_ESTIMATE_BYTES = _int_env(
    "REALTIME_EVENT_SIZE_ESTIMATE_BYTES", 2048,
)
# How many queues share the budget: socket.io + push gateway.
REALTIME_QUEUE_COUNT = _int_env("REALTIME_QUEUE_COUNT", 2)


def realtime_queue_maxsize() -> int:
    """The bound BOTH realtime consumers use.

    ``REALTIME_QUEUE_MAX`` still wins when set, so an operator can pin an exact
    number without reasoning about the derivation — and so the pre-SA-2265
    override keeps working.
    """
    override = os.getenv("REALTIME_QUEUE_MAX", "")
    if override:
        try:
            return max(1, int(override))
        except (TypeError, ValueError):
            logger.warning(
                "REALTIME_QUEUE_MAX=%r is not an integer — deriving from the "
                "memory budget instead", override,
            )
    derived = REALTIME_QUEUE_BUDGET_BYTES // (
        REALTIME_EVENT_SIZE_ESTIMATE_BYTES * REALTIME_QUEUE_COUNT
    )
    return max(1, derived)


class RealtimeQueueStats:
    """Depth + overflow for one realtime queue, exported via SA-2256.

    Depth is read from the queue at SCRAPE time, not sampled: a queue that
    fills and drains between samples is exactly the one worth seeing.
    """

    # ``__weakref__`` is REQUIRED: CacheBudgetRegistry holds members weakly, and
    # __slots__ without it makes this class un-weak-referenceable. The consumers
    # register inside a try/except so observability can never block boot — which
    # means this failure would be SILENT, and the queue metrics would simply
    # never appear. Second time this trap has been hit in SA-2254; both are now
    # pinned by tests.
    __slots__ = ("_name", "_queue", "_maxsize", "_dropped", "_lock", "__weakref__")

    def __init__(self, name: str, queue, maxsize: int) -> None:
        self._name = name
        self._queue = queue
        self._maxsize = maxsize
        self._dropped = 0
        self._lock = threading.Lock()

    def record_drop(self) -> None:
        with self._lock:
            self._dropped += 1

    @property
    def dropped(self) -> int:
        return self._dropped

    def budget_snapshot(self) -> dict:
        try:
            depth = self._queue.qsize()
        except Exception:  # pragma: no cover - defensive
            depth = -1
        return {
            "entries": depth,
            "maxsize": self._maxsize,
            "dropped": self._dropped,
            "budget_bytes_estimate": depth * REALTIME_EVENT_SIZE_ESTIMATE_BYTES,
        }
