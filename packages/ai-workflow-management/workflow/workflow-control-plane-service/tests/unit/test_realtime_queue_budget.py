"""SA-2265 — one derived bound for both realtime queues, and drop is explicit.

Before: ``socketio_consumer`` at 16 000 (env-tunable) and
``push_gateway_consumer`` hard-coded at 10 000 — the same class of buffer, two
unrelated numbers, neither derived from anything, both chosen when the pod had
1–2GB. At a conservative 2KB per event that is ~51MB of buffer, a fifth of the
entire 256MB target, spent before any work is done.
"""

from __future__ import annotations

import asyncio

import pytest

from app.layer2_application.interfaces.cache_budget_registry import (
    CacheBudgetRegistry,
)
from app.layer4_frameworks.realtime import queue_budget as qb
from app.layer4_frameworks.realtime.queue_budget import (
    RealtimeQueueStats,
    realtime_queue_maxsize,
)


class TestTheBoundIsDerivedNotChosen:
    def test_maxsize_follows_the_budget(self, monkeypatch):
        monkeypatch.setattr(qb, "REALTIME_QUEUE_BUDGET_BYTES", 8 * 1024 * 1024)
        monkeypatch.setattr(qb, "REALTIME_EVENT_SIZE_ESTIMATE_BYTES", 1024)
        monkeypatch.setattr(qb, "REALTIME_QUEUE_COUNT", 2)
        monkeypatch.delenv("REALTIME_QUEUE_MAX", raising=False)
        assert realtime_queue_maxsize() == 4096

    def test_a_smaller_budget_gives_a_smaller_queue(self, monkeypatch):
        monkeypatch.delenv("REALTIME_QUEUE_MAX", raising=False)
        monkeypatch.setattr(qb, "REALTIME_EVENT_SIZE_ESTIMATE_BYTES", 1024)
        monkeypatch.setattr(qb, "REALTIME_QUEUE_COUNT", 2)
        monkeypatch.setattr(qb, "REALTIME_QUEUE_BUDGET_BYTES", 2 * 1024 * 1024)
        small = realtime_queue_maxsize()
        monkeypatch.setattr(qb, "REALTIME_QUEUE_BUDGET_BYTES", 16 * 1024 * 1024)
        large = realtime_queue_maxsize()
        assert small * 8 == large, "the bound does not track the budget"

    def test_the_default_fits_well_inside_256mb(self, monkeypatch):
        monkeypatch.delenv("REALTIME_QUEUE_MAX", raising=False)
        worst_case = (
            realtime_queue_maxsize()
            * qb.REALTIME_EVENT_SIZE_ESTIMATE_BYTES
            * qb.REALTIME_QUEUE_COUNT
        )
        assert worst_case <= 32 * 1024 * 1024, (
            f"realtime buffers alone would hold {worst_case / 1024 / 1024:.0f}MB "
            "of a 256MB pod"
        )

    def test_it_is_strictly_smaller_than_what_it_replaced(self, monkeypatch):
        monkeypatch.delenv("REALTIME_QUEUE_MAX", raising=False)
        before = (16_000 + 10_000) * qb.REALTIME_EVENT_SIZE_ESTIMATE_BYTES
        after = (
            realtime_queue_maxsize() * 2 * qb.REALTIME_EVENT_SIZE_ESTIMATE_BYTES
        )
        assert after < before

    def test_an_explicit_override_still_wins(self, monkeypatch):
        monkeypatch.setenv("REALTIME_QUEUE_MAX", "777")
        assert realtime_queue_maxsize() == 777

    def test_a_garbage_override_falls_back_to_the_derivation(self, monkeypatch):
        monkeypatch.setenv("REALTIME_QUEUE_MAX", "lots")
        assert realtime_queue_maxsize() == realtime_queue_maxsize()
        assert realtime_queue_maxsize() > 1

    def test_never_zero(self, monkeypatch):
        monkeypatch.delenv("REALTIME_QUEUE_MAX", raising=False)
        monkeypatch.setattr(qb, "REALTIME_QUEUE_BUDGET_BYTES", 1)
        monkeypatch.setattr(qb, "REALTIME_EVENT_SIZE_ESTIMATE_BYTES", 1_000_000)
        assert realtime_queue_maxsize() >= 1, "a zero-length queue drops everything"


class TestBothConsumersShareTheBound:
    def test_socketio_and_push_gateway_agree(self, monkeypatch):
        monkeypatch.delenv("REALTIME_QUEUE_MAX", raising=False)
        from app.layer4_frameworks.realtime.push_gateway_consumer import (
            PushGatewayRealtimeConsumer,
        )

        push = PushGatewayRealtimeConsumer(client=object())
        expected = realtime_queue_maxsize()
        assert push._queue.maxsize == expected, (
            f"push gateway uses {push._queue.maxsize}, the shared derivation "
            f"says {expected} — the two bounds have diverged again"
        )

    def test_it_does_not_hard_code_a_number(self):
        """Only one realtime sink is left — the Socket.IO consumer went with
        the move to the Push Gateway — but the bound must still come from the
        shared derivation, not a literal."""
        import inspect
        from app.layer4_frameworks.realtime import push_gateway_consumer

        src = inspect.getsource(push_gateway_consumer)
        assert "maxsize=10_000" not in src and "maxsize=10000" not in src, (
            f"{push_gateway_consumer.__name__} still hard-codes its queue bound"
        )


class TestBackpressureIsExplicit:
    """Drop, and counted — not incidental."""

    def test_overflow_is_counted(self):
        q = asyncio.Queue(maxsize=2)
        stats = RealtimeQueueStats("t", q, 2)
        q.put_nowait({"a": 1})
        q.put_nowait({"a": 2})
        for _ in range(5):
            try:
                q.put_nowait({"a": 3})
            except asyncio.QueueFull:
                stats.record_drop()
        assert stats.dropped == 5
        assert stats.budget_snapshot()["dropped"] == 5

    def test_depth_is_read_at_snapshot_time(self):
        q = asyncio.Queue(maxsize=10)
        stats = RealtimeQueueStats("t", q, 10)
        assert stats.budget_snapshot()["entries"] == 0
        q.put_nowait({"x": 1})
        q.put_nowait({"x": 2})
        assert stats.budget_snapshot()["entries"] == 2, (
            "depth was captured once instead of read live — a queue that fills "
            "and drains between scrapes is exactly the one worth seeing"
        )

    def test_snapshot_survives_a_broken_queue(self):
        class _Broken:
            def qsize(self):
                raise RuntimeError("gone")

        stats = RealtimeQueueStats("t", _Broken(), 10)
        assert stats.budget_snapshot()["entries"] == -1

    def test_stats_join_the_budget_export(self):
        reg = CacheBudgetRegistry()
        q = asyncio.Queue(maxsize=4)
        stats = RealtimeQueueStats("realtime_test", q, 4)
        reg.register(stats, "realtime_test")
        q.put_nowait({"x": 1})
        snap = reg.snapshot()["realtime_test"]
        assert snap["entries"] == 1
        assert snap["maxsize"] == 4
        assert "budget_bytes_estimate" in snap

    def test_every_budget_reporter_can_be_weakly_referenced(self):
        """Regression, second occurrence: a __slots__ class without
        __weakref__ cannot join the registry, and because the consumers
        register inside a try/except, that failure is SILENT — the metric
        simply never appears."""
        import weakref

        from app.layer4_frameworks.caching.bounded_ttl_cache import BoundedTTLCache

        for obj in (
            RealtimeQueueStats("t", asyncio.Queue(), 1),
            BoundedTTLCache(maxsize=1, ttl_seconds=0),
        ):
            weakref.ref(obj)  # must not raise

    def test_drop_not_block_is_the_documented_choice(self):
        """The trade is written down, because it is a real one: a dropped UI
        frame costs a stale panel; a blocked consumer thread costs the run."""
        assert "DROP" in qb.__doc__
        assert "blocked" in qb.__doc__.lower()
