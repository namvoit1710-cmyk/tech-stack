"""SA-1471 — DLQ durability (local spool + replay) + circuit alerting.

The story's acceptance criteria, as tests:
1. A simulated broker outage loses ZERO durable events — every DLQ-miss
   path (disabled / no client / DLQ POST failure) parks the envelope in
   the local spool instead of dropping it.
2. Circuit-open pages — the ``eventmesh.circuit.open`` gauge flips to 1.0
   on OPEN and back to 0.0 on CLOSE.
3. DLQ replay drains successfully — the spool re-enqueues into the send
   buffer on circuit close and at startup; the replay tool decodes
   ``publisher.dlq`` envelopes back to their original destination.
"""

from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer4_frameworks.messaging.event_mesh_dlq_spool import DlqSpool
from app.layer4_frameworks.messaging.event_mesh_metrics import EventMeshMetrics
from app.layer4_frameworks.messaging.event_mesh_names import EventMeshNames
from app.layer4_frameworks.messaging.event_mesh_publisher_pump import (
    EventMeshPublisher,
    _SendItem,
)


def _make_tokens() -> MagicMock:
    """Token provider stub good enough for start()/health_check()."""
    tokens = MagicMock()
    tokens.token_expires_at = 0.0
    tokens.access_token = ""
    tokens.run_refresh_loop = AsyncMock()  # create_task needs a coroutine
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
        buffer_size=100,
        buffer_policy="drop",
        flush_batch_size=10,
        flush_concurrency=2,
        max_connections=10,
        max_keepalive_connections=2,
        enable_dlq=True,
        circuit_failure_threshold=5,
        circuit_recovery_timeout_s=30.0,
        dlq_spool_dir=str(tmp_path / "spool"),
        dlq_best_effort_topics="realtime.events",
    )
    defaults.update(overrides)
    return EventMeshPublisher(**defaults)


def _item(name="run.status", payload=b'{"v": 1}') -> _SendItem:
    return _SendItem(kind="topic", name=name, payload=payload)


class TestDlqSpool:
    def test_disabled_when_dir_empty(self):
        spool = DlqSpool("")
        assert not spool.enabled
        assert spool.append({"a": 1}) is False
        assert spool.drain() == []

    def test_append_drain_fifo(self, tmp_path):
        spool = DlqSpool(str(tmp_path))
        for i in range(5):
            assert spool.append({"i": i}) is True
        assert spool.depth() == 5
        first = spool.drain(max_entries=3)
        assert [e["i"] for e in first] == [0, 1, 2]
        assert spool.depth() == 2
        rest = spool.drain()
        assert [e["i"] for e in rest] == [3, 4]
        assert spool.depth() == 0

    def test_depth_survives_reopen(self, tmp_path):
        DlqSpool(str(tmp_path)).append({"x": 1})
        reopened = DlqSpool(str(tmp_path))
        assert reopened.depth() == 1
        assert reopened.drain()[0] == {"x": 1}

    def test_full_spool_refuses_append(self, tmp_path):
        spool = DlqSpool(str(tmp_path), max_entries=2)
        assert spool.append({"i": 0}) and spool.append({"i": 1})
        assert spool.append({"i": 2}) is False
        assert spool.depth() == 2


class TestNoSilentLossOnDlqMiss:
    @pytest.mark.asyncio
    async def test_dlq_disabled_spools_durable_instead_of_dropping(self, tmp_path):
        pump = _make_pump(tmp_path, enable_dlq=False)
        await pump._dlq_send(_item("run.status"), error="http_500")
        assert pump._spool.depth() == 1
        env = pump._spool.drain()[0]
        assert env["original_name"] == "run.status"
        assert base64.b64decode(env["payload_base64"]) == b'{"v": 1}'

    @pytest.mark.asyncio
    async def test_dlq_post_failure_spools_durable(self, tmp_path):
        pump = _make_pump(tmp_path)
        pump._clients.async_ = AsyncMock()
        pump._tokens.auth_headers_async = AsyncMock(return_value={})
        pump._clients.async_.post = AsyncMock(side_effect=RuntimeError("broker down"))
        await pump._dlq_send(_item("node.completed"), error="http_503")
        assert pump._spool.depth() == 1

    @pytest.mark.asyncio
    async def test_best_effort_topic_still_drops_counted(self, tmp_path):
        metrics = MagicMock()
        pump = _make_pump(tmp_path, enable_dlq=False, metrics=metrics)
        await pump._dlq_send(_item("realtime.events"), error="http_500")
        assert pump._spool.depth() == 0  # not spooled
        metrics.record_dropped.assert_called_once_with("dlq_disabled", "realtime.events")

    @pytest.mark.asyncio
    async def test_spool_disabled_preserves_legacy_drop(self, tmp_path):
        metrics = MagicMock()
        pump = _make_pump(tmp_path, enable_dlq=False, dlq_spool_dir="", metrics=metrics)
        await pump._dlq_send(_item("run.status"), error="http_500")
        metrics.record_dropped.assert_called_once_with("dlq_disabled", "run.status")


class TestCircuitAlerting:
    def test_gauge_flips_on_open_and_close(self, tmp_path):
        metrics = MagicMock()
        pump = _make_pump(tmp_path, metrics=metrics)
        pump._on_circuit_state_change("CLOSED", "OPEN")
        metrics.record_circuit_state.assert_called_with("OPEN")
        pump._on_circuit_state_change("OPEN", "HALF_OPEN")
        pump._on_circuit_state_change("HALF_OPEN", "CLOSED")
        metrics.record_circuit_state.assert_called_with("CLOSED")

    def test_metrics_gauge_values(self):
        monitor = MagicMock()
        m = EventMeshMetrics(monitor)
        m.record_circuit_state("OPEN")
        monitor.track.assert_called_with("eventmesh.circuit.open", 1.0)
        m.record_circuit_state("CLOSED")
        monitor.track.assert_called_with("eventmesh.circuit.open", 0.0)


class TestSpoolReplay:
    @pytest.mark.asyncio
    async def test_circuit_close_replays_spool_into_buffer(self, tmp_path):
        pump = _make_pump(tmp_path)
        # Park two durable envelopes while "the broker is down".
        pump._spool.append(pump._dlq_envelope(_item("run.status", b'{"n":1}'), "e"))
        pump._spool.append(pump._dlq_envelope(_item("node.completed", b'{"n":2}'), "e"))
        pump._send_queue = asyncio.Queue(maxsize=100)
        pump._running_async = True

        pump._on_circuit_state_change("OPEN", "CLOSED")
        # The replay task was scheduled on this loop — let it run.
        for _ in range(20):
            if pump._send_queue.qsize() == 2:
                break
            await asyncio.sleep(0.05)

        assert pump._send_queue.qsize() == 2
        assert pump._spool.depth() == 0
        replayed_first = await pump._send_queue.get()
        assert replayed_first.name == "run.status"  # FIFO preserved
        assert replayed_first.payload == b'{"n":1}'

    @pytest.mark.asyncio
    async def test_startup_replays_leftover_spool(self, tmp_path):
        # A previous run parked an envelope, then the pod restarted.
        leftover = DlqSpool(str(tmp_path / "spool"))
        leftover.append({
            "original_kind": "topic", "original_name": "run.status",
            "error": "e", "failed_at": 0,
            "payload_base64": base64.b64encode(b'{"x":9}').decode(),
            "payload_size_bytes": 8,
        })
        pump = _make_pump(tmp_path)
        assert pump._spool.depth() == 1
        await pump.start()
        try:
            for _ in range(20):
                if pump._spool.depth() == 0:
                    break
                await asyncio.sleep(0.05)
            assert pump._spool.depth() == 0
        finally:
            await pump.stop(drain_timeout_s=0.1)

    @pytest.mark.asyncio
    async def test_malformed_spool_entry_skipped_not_fatal(self, tmp_path):
        pump = _make_pump(tmp_path)
        pump._spool.append({"garbage": True})  # no payload_base64
        pump._spool.append(pump._dlq_envelope(_item("run.status"), "e"))
        pump._send_queue = asyncio.Queue(maxsize=100)
        pump._running_async = True
        await pump._replay_spool("test")
        assert pump._send_queue.qsize() == 1  # good entry replayed, bad skipped


class TestSimulatedBrokerOutageEndToEnd:
    @pytest.mark.asyncio
    async def test_outage_loses_zero_durable_events(self, tmp_path):
        """AC: publishes fail terminally AND the DLQ POST fails (broker fully
        down) → everything durable is in the spool; when the circuit closes,
        every envelope is re-enqueued. Zero loss."""
        metrics = MagicMock()
        pump = _make_pump(tmp_path, metrics=metrics)
        pump._clients.async_ = AsyncMock()
        pump._tokens.auth_headers_async = AsyncMock(return_value={})
        pump._clients.async_.post = AsyncMock(side_effect=RuntimeError("outage"))

        n = 7
        for i in range(n):
            await pump._dlq_send(_item("run.status", json.dumps({"i": i}).encode()), "http_503")
        assert pump._spool.depth() == n
        metrics.record_dropped.assert_not_called()  # nothing lost

        # Broker heals → circuit closes → replay drains into the buffer.
        pump._send_queue = asyncio.Queue(maxsize=100)
        pump._running_async = True
        await pump._replay_spool("circuit_closed")
        assert pump._send_queue.qsize() == n
        assert pump._spool.depth() == 0
        order = [json.loads((await pump._send_queue.get()).payload)["i"] for _ in range(n)]
        assert order == list(range(n))  # FIFO — original publish order


class TestReplayToolDecode:
    def test_decode_roundtrip(self):
        from tools.replay_publisher_dlq import _decode
        envelope = {
            "original_kind": "queue",
            "original_name": "orchestrator.workflow_event",
            "payload_base64": base64.b64encode(json.dumps({"a": 1}).encode()).decode(),
        }
        kind, name, message = _decode(envelope)
        assert (kind, name, message) == ("queue", "orchestrator.workflow_event", {"a": 1})

    def test_decode_malformed_returns_none(self):
        from tools.replay_publisher_dlq import _decode
        assert _decode({"original_name": "x"}) is None


class TestHealthCheckExposesSpool:
    def test_health_includes_spool_state(self, tmp_path):
        pump = _make_pump(tmp_path)
        health = pump.health_check()
        assert health["dlq_spool"]["enabled"] is True
        assert health["dlq_spool"]["depth"] == 0
