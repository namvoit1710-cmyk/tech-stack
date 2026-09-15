# -*- coding: utf-8 -*-
"""``malloc_trim`` shipped in #1962 with no call site. This is the call site.

Every assertion here is anchored to a number measured on tenant-1 rather than
to a preference, because the whole reason the function was left unwired was that
the trade-off had not been measured yet.
"""
import asyncio
import logging

import pytest

from app.layer4_frameworks.monitoring import malloc_trim_janitor as mtj
from app.layer4_frameworks.monitoring.malloc_trim_janitor import (
    DURATION_HISTOGRAM,
    RELEASED_COUNTER,
    TRIM_COUNTER,
    MallocTrimJanitor,
)

MIB = 1024 * 1024


class _Monitor:
    def __init__(self):
        self.counts = []
        self.observations = []

    def increment(self, metric, tags=None, amount=1):
        self.counts.append((metric, tags, amount))

    def histogram(self, metric, value, tags=None):
        self.observations.append((metric, value))

    def outcomes(self):
        return [t["outcome"] for m, t, _ in self.counts
                if m == TRIM_COUNTER and t]


def _patch(monkeypatch, info, trim_result):
    monkeypatch.setattr(mtj, "read_malloc_info", lambda: info)
    monkeypatch.setattr(mtj, "malloc_trim", lambda pad=0: trim_result)


#: The measured idle state of tenant-1's control plane, 2026-08-21 08:43 +07,
#: after 7 hours during which the arena did not move by one byte.
_TENANT1_IDLE = {"arena": 1024032768, "in_use": 45184320,
                 "free": 978848448, "free_chunks": 1459, "mmapped": 2326528}


class TestTheGate:
    """``read_malloc_info``'s own rule decides, not the janitor's opinion."""

    def test_the_measured_production_state_is_trimmed(self, monkeypatch):
        """934 MiB free in 1 459 chunks = 655 KiB mean, 164x the 4 KiB page."""
        mean_kib = _TENANT1_IDLE["free"] / _TENANT1_IDLE["free_chunks"] / 1024
        assert mean_kib > 4, "the fixture stopped being the measured state"
        m = _Monitor()
        _patch(monkeypatch, _TENANT1_IDLE, {
            "rss_released_bytes": 900 * MIB, "arena_before": 976 * MIB,
            "arena_after": 76 * MIB, "duration_ms": 12.0, "returned": 1,
        })
        MallocTrimJanitor(m).trim_once()
        assert m.outcomes() == ["released"]

    def test_sub_page_fragmentation_is_not_trimmed(self, monkeypatch):
        """glibc releases whole PAGES. A mean chunk under one page is
        unreclaimable however many bytes it totals, so calling only spends
        event-loop time — and on instances:1 that is the whole tenant."""
        m = _Monitor()
        called = []
        monkeypatch.setattr(mtj, "read_malloc_info", lambda: {
            "free": 512 * MIB, "free_chunks": 512 * MIB // 100,  # 100 B mean
        })
        monkeypatch.setattr(
            mtj, "malloc_trim",
            lambda pad=0: called.append(pad) or {"duration_ms": 0},
        )
        MallocTrimJanitor(m).trim_once()
        assert called == [], "trimmed sub-page fragmentation"
        assert m.outcomes() == ["skipped_fragmented"]

    def test_a_small_arena_is_not_trimmed(self, monkeypatch):
        """Idle in_use on tenant-1 is 42-52 MiB; below 64 MiB free there is
        nothing to reclaim that is not just the working set."""
        m = _Monitor()
        called = []
        monkeypatch.setattr(mtj, "read_malloc_info",
                            lambda: {"free": 8 * MIB, "free_chunks": 4})
        monkeypatch.setattr(
            mtj, "malloc_trim",
            lambda pad=0: called.append(pad) or {"duration_ms": 0},
        )
        MallocTrimJanitor(m).trim_once()
        assert called == []
        assert m.outcomes() == ["skipped_small"]

    def test_zero_chunks_is_not_a_division_by_zero(self, monkeypatch):
        m = _Monitor()
        monkeypatch.setattr(mtj, "read_malloc_info",
                            lambda: {"free": 512 * MIB, "free_chunks": 0})
        monkeypatch.setattr(mtj, "malloc_trim", lambda pad=0: None)
        assert MallocTrimJanitor(m).trim_once()["outcome"] == \
            "skipped_fragmented"


class TestItMeasuresRssNotArena:
    """The regression test for what this janitor actually did in production.

    Observed live on tenant-1, 2026-08-21 12:43, with the janitor running:

        rss    927.7 MiB -> 169.4 MiB      (-758 MiB, given back to the OS)
        arena 1144.9 MiB -> 1144.9 MiB     (did not move one byte)
        logged: "malloc_trim released 0 MiB", outcome = "nothing"

    glibc frees INTERIOR pages with ``MADV_DONTNEED``: the address space stays
    mapped, the residency goes. ``mallinfo2.arena`` measures the address space,
    so it is blind to the only thing that matters. Read off the container
    immediately after::

        [heap]  Size 617 508 kB   Rss 41 532 kB   Private_Dirty 41 532 kB

    603 MiB mapped, 40 MiB resident — while ``arena`` still reported 716 MiB.
    """

    #: The exact numbers from that trim.
    _LIVE = {
        "rss_released_bytes": 758 * MIB,
        "rss_before": 927 * MIB, "rss_after": 169 * MIB,
        "arena_released_bytes": 0,
        "arena_before": 1144 * MIB, "arena_after": 1144 * MIB,
        "duration_ms": 74.2, "returned": 1,
    }

    def test_a_flat_arena_with_a_collapsed_rss_counts_as_released(
            self, monkeypatch):
        m = _Monitor()
        _patch(monkeypatch, _TENANT1_IDLE, dict(self._LIVE))
        MallocTrimJanitor(m).trim_once()
        assert m.outcomes() == ["released"], (
            "758 MiB went back to the OS and this reported 'nothing' — the "
            "janitor is reading the arena, which cannot see MADV_DONTNEED"
        )

    def test_the_counter_carries_the_rss_bytes(self, monkeypatch):
        m = _Monitor()
        _patch(monkeypatch, _TENANT1_IDLE, dict(self._LIVE))
        j = MallocTrimJanitor(m)
        j.trim_once()
        rel = [(t, a) for name, t, a in m.counts if name == RELEASED_COUNTER]
        assert rel == [(None, 758 * MIB)]
        assert j.snapshot()["released_bytes"] == 758 * MIB

    def test_the_log_line_names_rss(self, monkeypatch, caplog):
        m = _Monitor()
        _patch(monkeypatch, _TENANT1_IDLE, dict(self._LIVE))
        with caplog.at_level(logging.INFO):
            MallocTrimJanitor(m).trim_once()
        msg = [r.getMessage() for r in caplog.records
               if "malloc_trim" in r.getMessage()][0]
        assert "758 MiB of RSS" in msg
        assert "rss 927 -> 169 MiB" in msg, (
            "an operator must be able to see the reclaim without knowing that "
            "arena is the wrong number to look at"
        )

    def test_arena_is_the_fallback_when_rss_is_unreadable(self, monkeypatch):
        """No ``/proc`` (a test box, a non-Linux dev machine) must not turn a
        real arena shrink into ``nothing``."""
        m = _Monitor()
        _patch(monkeypatch, _TENANT1_IDLE, {
            "rss_released_bytes": None, "rss_before": None, "rss_after": None,
            "arena_released_bytes": 300 * MIB,
            "arena_before": 1000 * MIB, "arena_after": 700 * MIB,
            "duration_ms": 20.0, "returned": 1,
        })
        MallocTrimJanitor(m).trim_once()
        assert m.outcomes() == ["released"]

    def test_a_growing_rss_is_not_a_negative_release(self, monkeypatch):
        """Another thread can allocate across the trim, so the delta can come
        back negative. It must not reach the counter: Prometheus rejects a
        negative increment and the sample is lost silently.

        The guard is the ``released > 0`` branch, not a clamp. A ``max(0, ...)``
        was written here first and removed once a mutation showed it changed
        nothing observable — a negative value takes the ``else`` path either
        way, so the clamp was code that looked like a safety property and was
        not one.
        """
        m = _Monitor()
        _patch(monkeypatch, _TENANT1_IDLE, {
            "rss_released_bytes": -50 * MIB, "arena_released_bytes": 0,
            "duration_ms": 3.0, "returned": 0,
        })
        j = MallocTrimJanitor(m)
        j.trim_once()
        assert m.outcomes() == ["nothing"]
        assert j.snapshot()["released_bytes"] == 0


class TestNotGlibc:
    def test_absent_allocator_is_not_reported_as_nothing_to_release(
            self, monkeypatch):
        """A zero would read as 'nothing was reclaimable' when the truth is
        'this platform cannot be asked' — the discipline read_malloc_info
        already uses."""
        m = _Monitor()
        monkeypatch.setattr(mtj, "read_malloc_info", lambda: None)
        j = MallocTrimJanitor(m)
        assert j.trim_once()["outcome"] == "unavailable"
        assert j.snapshot()["unavailable"] is True
        assert RELEASED_COUNTER not in [x[0] for x in m.counts]

    @pytest.mark.asyncio
    async def test_the_loop_stops_instead_of_waking_forever(self, monkeypatch):
        """Nothing within a process makes malloc_trim appear later, so a timer
        that keeps waking to re-learn that is pure loop time."""
        m = _Monitor()
        monkeypatch.setattr(mtj, "read_malloc_info", lambda: None)
        j = MallocTrimJanitor(m, interval_s=1.0)
        await asyncio.wait_for(j.run_forever(), timeout=5)
        assert j.snapshot()["unavailable"] is True


class TestBackoff:
    """The interval alone is not a safety property.

    This call has never been measured on chunks as large as this pod's, and if
    it costs half a second a fixed timer inflicts that on the tenant forever.
    """

    def _janitor(self, monkeypatch, duration_ms, m=None):
        m = m or _Monitor()
        _patch(monkeypatch, _TENANT1_IDLE, {
            "rss_released_bytes": 100 * MIB, "arena_before": 976 * MIB,
            "arena_after": 876 * MIB, "duration_ms": duration_ms,
            "returned": 1,
        })
        return MallocTrimJanitor(m, interval_s=300.0, max_duration_ms=100.0), m

    def test_an_over_budget_trim_doubles_the_interval(self, monkeypatch):
        j, _ = self._janitor(monkeypatch, 250.0)
        j.trim_once()
        assert j.snapshot()["interval_s"] == 600.0
        j.trim_once()
        assert j.snapshot()["interval_s"] == 1200.0

    def test_the_backoff_is_capped(self, monkeypatch):
        j, _ = self._janitor(monkeypatch, 900.0)
        for _ in range(12):
            j.trim_once()
        assert j.snapshot()["backoff"] == 8
        assert j.snapshot()["interval_s"] == 2400.0

    def test_it_recovers_when_the_cost_comes_back_down(self, monkeypatch):
        j, _ = self._janitor(monkeypatch, 250.0)
        j.trim_once()
        assert j.snapshot()["backoff"] == 2
        _patch(monkeypatch, _TENANT1_IDLE, {
            "rss_released_bytes": 1 * MIB, "arena_before": 1, "arena_after": 0,
            "duration_ms": 5.0, "returned": 1,
        })
        j.trim_once()
        assert j.snapshot()["interval_s"] == 300.0
        assert j.snapshot()["backoff"] == 1

    def test_an_over_budget_trim_warns(self, monkeypatch, caplog):
        j, _ = self._janitor(monkeypatch, 250.0)
        with caplog.at_level(logging.INFO):
            j.trim_once()
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warns, "an over-budget trim must be visible to an operator"
        assert "event_loop_lag_seconds" in warns[0].getMessage(), (
            "the warning must name the metric that shows the harm"
        )

    def test_a_within_budget_trim_does_not_warn(self, monkeypatch, caplog):
        j, _ = self._janitor(monkeypatch, 12.0)
        with caplog.at_level(logging.INFO):
            j.trim_once()
        assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


class TestMetrics:
    def test_released_bytes_are_summed_untagged(self, monkeypatch):
        """``_get_counter`` freezes a label set on first observation and drops
        later mismatched increments SILENTLY, so the tags must never vary."""
        m = _Monitor()
        _patch(monkeypatch, _TENANT1_IDLE, {
            "rss_released_bytes": 700 * MIB, "arena_before": 976 * MIB,
            "arena_after": 276 * MIB, "duration_ms": 9.0, "returned": 1,
        })
        j = MallocTrimJanitor(m)
        j.trim_once()
        j.trim_once()
        rel = [(t, a) for name, t, a in m.counts if name == RELEASED_COUNTER]
        assert rel == [(None, 700 * MIB), (None, 700 * MIB)]
        assert j.snapshot()["released_bytes"] == 1400 * MIB

    def test_every_outcome_uses_the_same_label_key(self, monkeypatch):
        m = _Monitor()
        keys = set()
        for info, trim in (
            (None, None),
            ({"free": 1 * MIB, "free_chunks": 1}, None),
            ({"free": 512 * MIB, "free_chunks": 512 * 1024}, None),
            (_TENANT1_IDLE, {"rss_released_bytes": 0, "duration_ms": 1.0,
                             "returned": 0}),
            (_TENANT1_IDLE, {"rss_released_bytes": MIB, "duration_ms": 1.0,
                             "returned": 1}),
        ):
            _patch(monkeypatch, info, trim)
            MallocTrimJanitor(m).trim_once()
        for name, tags, _ in m.counts:
            if name == TRIM_COUNTER:
                keys.add(tuple(sorted(tags or {})))
        assert keys == {("outcome",)}

    def test_the_outcome_label_is_bounded(self, monkeypatch):
        """No byte counts, no durations, no run ids in a label."""
        m = _Monitor()
        _patch(monkeypatch, _TENANT1_IDLE,
               {"rss_released_bytes": MIB, "duration_ms": 1.0, "returned": 1})
        MallocTrimJanitor(m).trim_once()
        for name, tags, _ in m.counts:
            if name == TRIM_COUNTER:
                assert tags["outcome"] in {
                    "released", "nothing", "skipped_small",
                    "skipped_fragmented", "unavailable", "error",
                }

    def test_duration_is_recorded_in_seconds(self, monkeypatch):
        """``_get_histogram`` hard-codes 0.001-5.0 request-latency buckets with
        no way to pass others. Milliseconds would pile into +Inf and measure
        nothing — the defect ``eventmesh_flush_*`` already carries."""
        m = _Monitor()
        _patch(monkeypatch, _TENANT1_IDLE, {
            "rss_released_bytes": MIB, "duration_ms": 54.6, "returned": 1,
        })
        MallocTrimJanitor(m).trim_once()
        assert m.observations == [(DURATION_HISTOGRAM, 0.0546)]
        value = m.observations[0][1]
        assert 0.001 <= value <= 5.0, (
            "outside the hard-coded bucket range, so it would land in +Inf"
        )

    def test_released_and_nothing_are_distinct(self, monkeypatch):
        """``returned=1`` with 0 bytes released means pages went back but the
        arena high-water did not move — a different fact from 'there was
        nothing to release'."""
        m = _Monitor()
        _patch(monkeypatch, _TENANT1_IDLE,
               {"rss_released_bytes": 0, "duration_ms": 3.0, "returned": 1})
        MallocTrimJanitor(m).trim_once()
        assert m.outcomes() == ["nothing"]
        assert RELEASED_COUNTER not in [x[0] for x in m.counts]


class TestItNeverBreaksTheLoop:
    def test_a_raising_allocator_read_is_swallowed(self, monkeypatch):
        """A janitor that dies on its first bad trim silently stops bounding
        RSS, and the symptom surfaces days later as memory that only grows."""
        def boom():
            raise RuntimeError("mallinfo2 exploded")
        m = _Monitor()
        monkeypatch.setattr(mtj, "read_malloc_info", boom)
        j = MallocTrimJanitor(m)
        assert j.trim_once()["outcome"] == "error"
        assert j.snapshot()["errors"] == 1
        assert m.outcomes() == ["error"]

    def test_a_broken_monitor_does_not_break_the_trim(self, monkeypatch):
        class _Bad:
            def increment(self, *a, **k):
                raise RuntimeError("prometheus is down")

            def histogram(self, *a, **k):
                raise RuntimeError("prometheus is down")

        _patch(monkeypatch, _TENANT1_IDLE, {
            "rss_released_bytes": 700 * MIB, "duration_ms": 9.0, "returned": 1,
        })
        j = MallocTrimJanitor(_Bad())
        assert j.trim_once()["outcome"] == "released"
        assert j.snapshot()["released_bytes"] == 700 * MIB

    def test_no_monitor_at_all_is_fine(self, monkeypatch):
        _patch(monkeypatch, _TENANT1_IDLE, {
            "rss_released_bytes": MIB, "duration_ms": 1.0, "returned": 1,
        })
        assert MallocTrimJanitor(None).trim_once()["outcome"] == "released"


class TestConfiguration:
    def test_a_zero_interval_cannot_spin_the_loop(self):
        assert MallocTrimJanitor(None, interval_s=0).snapshot()["interval_s"] \
            == 1.0
        assert MallocTrimJanitor(None, interval_s=-5).snapshot()["interval_s"] \
            == 1.0

    def test_the_pad_reaches_glibc(self, monkeypatch):
        seen = []
        monkeypatch.setattr(mtj, "read_malloc_info", lambda: _TENANT1_IDLE)
        monkeypatch.setattr(mtj, "malloc_trim", lambda pad=0: (
            seen.append(pad) or {"rss_released_bytes": MIB, "duration_ms": 1.0,
                                 "returned": 1}))
        MallocTrimJanitor(None, pad_bytes=32 * MIB).trim_once()
        assert seen == [32 * MIB]

    def test_it_is_off_by_default_in_config(self):
        """Unlike CACHE_EXPIRY_SWEEP_ENABLED. That janitor's risk is holding a
        secret; this one's is event-loop time on a plane at instances:1, and
        the cost has never been measured on chunks this large."""
        import os
        from importlib import reload

        from app.layer4_frameworks.config import app_config

        os.environ.pop("MALLOC_TRIM_ENABLED", None)
        reload(app_config)
        assert app_config.Settings().MALLOC_TRIM_ENABLED is False


class TestTheLoop:
    @pytest.mark.asyncio
    async def test_stop_ends_it_and_it_reports_totals(self, monkeypatch,
                                                      caplog):
        m = _Monitor()
        _patch(monkeypatch, _TENANT1_IDLE, {
            "rss_released_bytes": 700 * MIB, "duration_ms": 9.0, "returned": 1,
        })
        j = MallocTrimJanitor(m, interval_s=1.0)
        with caplog.at_level(logging.INFO):
            task = asyncio.create_task(j.run_forever())
            await asyncio.sleep(1.5)
            j.stop()
            await asyncio.wait_for(task, timeout=5)
        assert j.snapshot()["trims"] >= 1
        assert any("malloc_trim janitor stopped" in r.getMessage()
                   for r in caplog.records)
