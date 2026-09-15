"""How long is the event loop unavailable to everything else?

This service runs FastAPI, Socket.IO, every broker consumer and
``ORCHESTRATION_WORKER_COUNT`` orchestration workers on **one** event loop.
Its capacity is therefore not "how many runs" but "how much loop time each
action costs", and until now nothing measured that. A stall is invisible in
every existing metric: request handlers still report their own duration, the
broker still reports lag, and none of them say *the loop was frozen*.

The measurement is the standard one — sleep for a known interval, see how much
longer it actually took:

    t0 = perf_counter(); await sleep(interval); lag = elapsed - interval

Two numbers, not one
--------------------
A histogram of ticks alone **under-reports the thing that matters**. If the
loop freezes for 200 ms once a second, a 10 ms ticker records one sample of
200 ms and ninety-nine of roughly zero — and p95 reads *zero* while the pod is
visibly stuttering. Percentiles over ticks are not percentiles over stalls.

So this records both:

* ``event_loop_lag_seconds`` — histogram, the distribution.
* ``event_loop_lag_peak_seconds`` — gauge, the **worst** lag seen in the last
  reporting window. This is the number that says "the loop froze for 843 ms".

and it logs a warning on any single stall over ``warn_threshold_s``, so a
freeze is visible from ``cf logs`` alone, without a metrics stack.

Reading the numbers
-------------------
``asyncio.sleep`` overshoots by a millisecond or two even on an idle loop —
timer granularity, not a stall. Treat readings below ``NOISE_FLOOR_S`` as
noise. The peak gauge is the one to alert on.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

_logger = logging.getLogger(__name__)

#: Below this, a reading is timer granularity rather than a real stall.
NOISE_FLOOR_S = 0.005

LAG_HISTOGRAM = "event_loop_lag_seconds"
LAG_PEAK_GAUGE = "event_loop_lag_peak_seconds"


class EventLoopLagMonitor:
    """Samples event-loop lag and reports distribution + peak.

    Deliberately cheap: one timer wakeup per ``interval_s`` and one histogram
    observation, both O(1). At the default 10 ms that is 100 wakeups/second,
    which is far below the cost of a single HANA round-trip — the monitor must
    never become the thing it is measuring.
    """

    __slots__ = ("_monitor", "_interval", "_report_every", "_warn_threshold",
                 "_stop", "_peak", "_peak_at", "_samples", "_warned")

    def __init__(
        self,
        monitor: Any,
        *,
        interval_s: float = 0.01,
        report_interval_s: float = 10.0,
        warn_threshold_s: float = 0.25,
    ) -> None:
        self._monitor = monitor
        self._interval = max(0.001, float(interval_s))
        self._report_every = max(1.0, float(report_interval_s))
        self._warn_threshold = float(warn_threshold_s)
        self._stop = asyncio.Event()
        self._peak = 0.0
        self._peak_at = 0.0
        self._samples = 0
        self._warned = 0

    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """Sample until :meth:`stop` is called. Start via ``create_task``."""
        _logger.info(
            "Event-loop lag monitor started (interval=%.0fms, report=%.0fs, "
            "warn>%.0fms)",
            self._interval * 1000, self._report_every,
            self._warn_threshold * 1000,
        )
        window_start = time.perf_counter()
        while not self._stop.is_set():
            before = time.perf_counter()
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._interval,
                )
                break  # stop was set
            except asyncio.TimeoutError:
                pass
            lag = time.perf_counter() - before - self._interval
            self._observe(lag)

            now = time.perf_counter()
            if now - window_start >= self._report_every:
                self._report()
                window_start = now
        self._report()
        _logger.info("Event-loop lag monitor stopped")

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------

    def _observe(self, lag: float) -> None:
        if lag < 0:
            # The loop woke us EARLY — clock granularity, not information.
            lag = 0.0
        self._samples += 1
        if lag > self._peak:
            self._peak = lag
            self._peak_at = time.time()
        self._record(LAG_HISTOGRAM, lag)
        if lag >= self._warn_threshold:
            self._warned += 1
            _logger.warning(
                "Event loop stalled for %.0fms — nothing else on this pod ran "
                "during it (no HTTP, no Socket.IO, no broker consumer, no "
                "other orchestration worker)",
                lag * 1000,
            )

    def _report(self) -> None:
        """Publish the window's peak, then reset it.

        Resetting is the point: a peak that never resets reports the worst
        moment since boot forever, which stops being actionable within an hour
        of the first spike.
        """
        self._track(LAG_PEAK_GAUGE, self._peak)
        if self._peak >= self._warn_threshold:
            _logger.warning(
                "Event-loop lag peak this window: %.0fms over %d samples "
                "(%d stalls above the %.0fms threshold)",
                self._peak * 1000, self._samples, self._warned,
                self._warn_threshold * 1000,
            )
        self._peak = 0.0
        self._samples = 0
        self._warned = 0

    # -- the monitor may be absent or a test double; never break the loop --

    def _record(self, metric: str, value: float) -> None:
        if self._monitor is None:
            return
        try:
            self._monitor.histogram(metric, value)
        except Exception:  # noqa: BLE001 - metrics must never break the loop
            pass

    def _track(self, metric: str, value: float) -> None:
        if self._monitor is None:
            return
        try:
            self._monitor.track(metric, value)
        except Exception:  # noqa: BLE001
            pass

    # -- test / probe surface ------------------------------------------

    def snapshot(self) -> dict:
        """Current window state. For tests and in-pod probes."""
        return {
            "peak_s": self._peak,
            "peak_at": self._peak_at,
            "samples": self._samples,
            "stalls_over_threshold": self._warned,
            "interval_s": self._interval,
            "warn_threshold_s": self._warn_threshold,
        }


__all__ = [
    "EventLoopLagMonitor",
    "LAG_HISTOGRAM",
    "LAG_PEAK_GAUGE",
    "NOISE_FLOOR_S",
]
