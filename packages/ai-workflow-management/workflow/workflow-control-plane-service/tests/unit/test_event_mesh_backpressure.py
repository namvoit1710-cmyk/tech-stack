"""SA-1469 — publish backpressure: stop silently dropping domain events.

The story's acceptance criteria, as tests:
1. Under induced backpressure, ZERO durable events are lost — buffer-full
   overflows durable envelopes into the SA-1471 spool and the flusher
   replays them (in original order) once the buffer drains.
2. Split policy: best-effort realtime topics may still drop (counted);
   ``buffer_policy="raise"`` keeps raising ``EventMeshBufferFull``.
3. buffer_full is alertable — ``eventmesh.buffer.full`` fires on every
   rejected enqueue, and a wide fan-out ends with no silent drops.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer4_frameworks.messaging.event_mesh_errors import EventMeshBufferFull
from app.layer4_frameworks.messaging.event_mesh_metrics import EventMeshMetrics
from app.layer4_frameworks.messaging.event_mesh_names import EventMeshNames
from app.layer4_frameworks.messaging.event_mesh_publisher_pump import (
    EventMeshPublisher,
    _SendItem,
)


def _make_tokens() -> MagicMock:
    tokens = MagicMock()
    tokens.token_expires_at = 0.0
    tokens.access_token = ""
    tokens.run_refresh_loop = AsyncMock()
    tokens.auth_headers_async = AsyncMock(return_value={})
    return tokens


def _make_pump(tmp_path, **overrides) -> EventMeshPublisher:
    defaults = dict(
        messaging_url="https://messaging.example.com",
        names=EventMeshNames("ns"),
        tokens=_make_tokens(),
        clients=MagicMock(),
        metrics=EventMeshMetrics(None),
        timeout=5.0,
        max_retries=1,
        max_payload_bytes=900_000,
        buffer_size=4,  # tiny buffer to induce backpressure easily
        buffer_policy="drop",
        flush_batch_size=2,
        flush_concurrency=1,
        max_connections=10,
        max_keepalive_connections=2,
        enable_dlq=False,  # isolate: overflow must not depend on the DLQ path
        circuit_failure_threshold=100,
        circuit_recovery_timeout_s=30.0,
        dlq_spool_dir=str(tmp_path / "spool"),
        dlq_best_effort_topics="realtime.events",
    )
    defaults.update(overrides)
    return EventMeshPublisher(**defaults)


def _prime(pump: EventMeshPublisher, maxsize: int = 4) -> None:
    """Give the pump a live queue without starting the flusher."""
    pump._send_queue = asyncio.Queue(maxsize=maxsize)
    pump._running_async = True


def _item(name="run.status", payload=b'{"v":1}') -> _SendItem:
    return _SendItem(kind="topic", name=name, payload=payload)


class TestBufferFullSplitPolicy:
    @pytest.mark.asyncio
    async def test_durable_overflows_to_spool_not_dropped(self, tmp_path):
        metrics = MagicMock()
        pump = _make_pump(tmp_path, metrics=metrics)
        _prime(pump)
        for i in range(4):
            pump._enqueue(_item(payload=json.dumps({"i": i}).encode()))
        # Buffer (4) is full — the 5th durable event must be spooled.
        pump._enqueue(_item(payload=b'{"i":4}'))
        assert pump._spool.depth() == 1
        metrics.record_buffer_full.assert_called_once_with("run.status")
        metrics.record_dropped.assert_not_called()  # NOT a loss

    @pytest.mark.asyncio
    async def test_best_effort_still_drops_counted(self, tmp_path):
        metrics = MagicMock()
        pump = _make_pump(tmp_path, metrics=metrics)
        _prime(pump)
        for _ in range(4):
            pump._enqueue(_item("realtime.events"))
        pump._enqueue(_item("realtime.events"))
        assert pump._spool.depth() == 0  # split policy: droppable class
        metrics.record_dropped.assert_called_once_with("buffer_full", "realtime.events")

    @pytest.mark.asyncio
    async def test_raise_policy_contract_unchanged(self, tmp_path):
        pump = _make_pump(tmp_path, buffer_policy="raise")
        _prime(pump)
        for _ in range(4):
            pump._enqueue(_item())
        with pytest.raises(EventMeshBufferFull):
            pump._enqueue(_item())

    @pytest.mark.asyncio
    async def test_spool_disabled_preserves_legacy_drop(self, tmp_path):
        metrics = MagicMock()
        pump = _make_pump(tmp_path, dlq_spool_dir="", metrics=metrics)
        _prime(pump)
        for _ in range(4):
            pump._enqueue(_item())
        pump._enqueue(_item())
        metrics.record_dropped.assert_called_once_with("buffer_full", "run.status")


class TestReplaySingleFlight:
    @pytest.mark.asyncio
    async def test_schedule_is_single_flight(self, tmp_path):
        pump = _make_pump(tmp_path)
        _prime(pump, maxsize=100)
        pump._spool.append(pump._dlq_envelope(_item(), "buffer_full"))

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_replay(trigger):
            started.set()
            await release.wait()

        pump._replay_spool = slow_replay  # type: ignore[assignment]
        pump._schedule_spool_replay("a")
        await asyncio.wait_for(started.wait(), timeout=2)
        first_task = pump._replay_task
        pump._schedule_spool_replay("b")  # must be a no-op while running
        assert pump._replay_task is first_task
        release.set()
        await asyncio.wait_for(first_task, timeout=2)


class TestInducedBackpressureZeroLoss:
    @pytest.mark.asyncio
    async def test_wide_fanout_no_silent_drops(self, tmp_path):
        """AC: buffer of 4, 30 durable publishes with a live flusher — every
        payload reaches the wire exactly once (order preserved per stream),
        zero record_dropped."""
        metrics = MagicMock()
        pump = _make_pump(tmp_path, metrics=metrics)

        posted: list[int] = []

        async def fake_post(url, content=None, headers=None):
            posted.append(json.loads(content)["i"])
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            return resp

        await pump.start()
        try:
            pump._clients.async_.post = AsyncMock(side_effect=fake_post)
            for i in range(30):
                pump._enqueue(_item(payload=json.dumps({"i": i}).encode()))
            # Wait until every event (direct or via spool replay) is posted.
            for _ in range(200):
                if len(posted) >= 30 and pump._spool.depth() == 0:
                    break
                await asyncio.sleep(0.05)
        finally:
            await pump.stop(drain_timeout_s=2.0)

        assert sorted(posted) == list(range(30))  # all 30, exactly once
        metrics.record_dropped.assert_not_called()  # zero silent drops
        assert metrics.record_buffer_full.call_count > 0  # alert DID fire
