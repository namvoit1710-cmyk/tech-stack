"""The loop-lag monitor must catch a stall a histogram alone would hide.

The control plane's capacity is bounded by event-loop time — one loop serves
FastAPI, Socket.IO, every broker consumer and all ORCHESTRATION_WORKER_COUNT
orchestration workers — and nothing measured it before SA-2257 P0.

The trap these tests exist for: a histogram of ticks under-reports stalls. A
10ms sampler watching a loop that freezes for 200ms once a second records ONE
high sample and ninety-nine near-zero ones, so p95 over ticks reads ~0 while
the pod visibly stutters. That is why the monitor also publishes a per-window
PEAK gauge, and the first test below is the proof that the peak is the number
that carries the signal.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.layer4_frameworks.monitoring.event_loop_lag import (
    EventLoopLagMonitor,
    LAG_HISTOGRAM,
    LAG_PEAK_GAUGE,
)
from app.layer4_frameworks.monitoring.in_memory import InMemoryMonitor


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(len(ordered) * q))
    return ordered[idx]


class TestItActuallyDetectsAStall:
    @pytest.mark.asyncio
    async def test_the_peak_catches_what_the_percentile_misses(self):
        """Block the loop once, then show the two numbers disagree."""
        monitor = InMemoryMonitor()
        lag = EventLoopLagMonitor(
            monitor, interval_s=0.005, report_interval_s=3600,
            warn_threshold_s=0.05,
        )
        task = asyncio.create_task(lag.run_forever())
        await asyncio.sleep(0.15)          # collect a healthy baseline

        time.sleep(0.2)                    # <-- freeze the loop, deliberately

        await asyncio.sleep(0.15)          # collect more healthy samples
        lag.stop()
        await task

        samples = [o.value for o in monitor.histogram_observations(LAG_HISTOGRAM)]
        assert len(samples) > 20, "sampler did not run often enough to judge"

        p95 = _percentile(samples, 0.95)
        peak = max(samples)

        assert peak >= 0.15, f"the 200ms freeze was not observed at all: {peak}"
        # The point of the whole design, asserted rather than asserted-about:
        assert p95 < peak / 2, (
            f"p95={p95:.3f}s vs peak={peak:.3f}s — if these were close, the "
            f"peak gauge would be redundant; they are not, which is why a "
            f"histogram alone would have reported this frozen loop as healthy"
        )

    @pytest.mark.asyncio
    async def test_the_peak_gauge_is_published(self):
        monitor = InMemoryMonitor()
        lag = EventLoopLagMonitor(
            monitor, interval_s=0.005, report_interval_s=0.05,
            warn_threshold_s=0.05,
        )
        task = asyncio.create_task(lag.run_forever())
        await asyncio.sleep(0.05)
        time.sleep(0.12)
        await asyncio.sleep(0.15)
        lag.stop()
        await task

        peaks = [g.value for g in monitor.gauges if g.metric == LAG_PEAK_GAUGE]
        assert peaks, f"no {LAG_PEAK_GAUGE} gauge was published"
        assert max(peaks) >= 0.1, (
            f"the peak gauge did not carry the ~120ms freeze: {peaks}"
        )

    @pytest.mark.asyncio
    async def test_an_idle_loop_reports_near_zero(self):
        """Otherwise every reading is a false alarm and nobody looks at it."""
        monitor = InMemoryMonitor()
        lag = EventLoopLagMonitor(
            monitor, interval_s=0.005, report_interval_s=3600,
            warn_threshold_s=0.25,
        )
        task = asyncio.create_task(lag.run_forever())
        await asyncio.sleep(0.25)
        lag.stop()
        await task

        samples = [o.value for o in monitor.histogram_observations(LAG_HISTOGRAM)]
        assert samples
        assert _percentile(samples, 0.5) < 0.02, (
            f"an idle loop should read near zero; median was "
            f"{_percentile(samples, 0.5):.4f}s"
        )
        assert all(v >= 0 for v in samples), "negative lag was recorded"


class TestItNeverBreaksTheLoopItWatches:
    @pytest.mark.asyncio
    async def test_a_throwing_monitor_is_swallowed(self):
        """A metrics backend failure must not take down the event loop."""
        class Exploding:
            def histogram(self, *_a, **_kw):
                raise RuntimeError("metrics backend down")

            def track(self, *_a, **_kw):
                raise RuntimeError("metrics backend down")

        lag = EventLoopLagMonitor(
            Exploding(), interval_s=0.005, report_interval_s=0.02,
        )
        task = asyncio.create_task(lag.run_forever())
        await asyncio.sleep(0.1)
        lag.stop()
        await task           # must not raise

    @pytest.mark.asyncio
    async def test_no_monitor_at_all_is_fine(self):
        lag = EventLoopLagMonitor(None, interval_s=0.005)
        task = asyncio.create_task(lag.run_forever())
        await asyncio.sleep(0.05)
        lag.stop()
        await task

    @pytest.mark.asyncio
    async def test_stop_is_prompt(self):
        """A slow stop delays every shutdown; the sampler waits on an Event
        rather than sleeping through its interval for exactly this reason."""
        lag = EventLoopLagMonitor(None, interval_s=5.0)
        task = asyncio.create_task(lag.run_forever())
        await asyncio.sleep(0.02)
        t0 = time.perf_counter()
        lag.stop()
        await asyncio.wait_for(task, timeout=1.0)
        assert time.perf_counter() - t0 < 0.5, (
            "stop() waited for the sampling interval — shutdown would stall"
        )


class TestTheWindowResets:
    @pytest.mark.asyncio
    async def test_peak_resets_between_windows(self):
        """A peak that never resets reports the worst moment since boot
        forever, and stops being actionable an hour after the first spike."""
        monitor = InMemoryMonitor()
        lag = EventLoopLagMonitor(
            monitor, interval_s=0.005, report_interval_s=0.06,
            warn_threshold_s=10.0,
        )
        task = asyncio.create_task(lag.run_forever())
        await asyncio.sleep(0.02)
        time.sleep(0.1)                     # spike in window 1
        await asyncio.sleep(0.25)           # windows 2..N are quiet
        lag.stop()
        await task

        snap = lag.snapshot()
        assert snap["peak_s"] == 0.0, (
            "the peak carried across windows; a stale peak is worse than no "
            "peak because it looks current"
        )


class TestConfiguration:
    def test_settings_expose_the_knobs(self):
        from app.layer4_frameworks.config.app_config import Settings
        s = Settings()
        assert isinstance(s.EVENT_LOOP_LAG_ENABLED, bool)
        assert s.EVENT_LOOP_LAG_INTERVAL_S > 0
        assert s.EVENT_LOOP_LAG_WARN_THRESHOLD_S > 0

    def test_it_is_on_by_default(self, monkeypatch):
        """A load test without it can only report whether the pod survived."""
        from app.layer4_frameworks.config.app_config import Settings
        monkeypatch.delenv("EVENT_LOOP_LAG_ENABLED", raising=False)
        assert Settings().EVENT_LOOP_LAG_ENABLED is True

    def test_main_starts_and_stops_it(self):
        import inspect
        import main

        source = inspect.getsource(main)
        assert "EventLoopLagMonitor(" in source
        assert "lag_monitor.stop()" in source, (
            "without stop() before cancel() the final window's peak is "
            "discarded, and the last window before a shutdown is often the "
            "interesting one"
        )
