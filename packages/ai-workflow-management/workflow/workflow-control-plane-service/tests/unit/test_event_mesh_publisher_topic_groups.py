"""The publish flusher dispatches per destination, not one message at a time.

Measured on tenant-1 2026-08-11: the control plane publishes **19 messages per
node across 9 destinations**, the flusher sends them strictly one at a time at
16.2 ms a POST, and the message that advances the run is enqueued *last* —
behind the FE-facing telemetry. The same self-hop was measured twice in one
run: 0.065 s with one message ahead of it, 0.185 s with five. That 0.12 s is
queueing, and it is paid once per node.

The sequential dispatch bought **cross-destination** ordering. No consumer can
observe that: every consumer in the system reads exactly one topic, and each
topic is its own queue on its own AMQP link, so cross-topic delivery order is
undefined however it was published. **Per-destination** order is the real
guarantee, and these tests pin it.

Design notes worth knowing before changing this file:

* The concurrency width is DERIVED per batch, not configured. Makespan is
  ``max(largest_group, total / width)``, so width past
  ``ceil(total / largest)`` buys nothing — no concurrency splits a group.
  ``flush_concurrency`` is a CEILING on held connections, not a tuning knob:
  a hardcoded width would be silently wrong the first time someone adds an
  event type.
* Groups dispatch shortest-first. That is a scheduling heuristic, not a
  priority policy — it names no topic and needs no classification when one is
  added. It releases the small run-advancing group ahead of the large
  telemetry group as a consequence of size.
* This shipped behind ``EVENT_MESH_FLUSH_GROUPED``, was measured live
  (``orchestration.hop_seconds``: ``continue`` 159ms → 105ms, ``execute_node``
  39ms → 26ms, wall −0.474s on a 5-node probe), and the flag was then deleted.
  There is one dispatch path now — do not reintroduce a sequential fallback.
* ``asyncio.Semaphore`` is FIFO-fair and ``gather`` schedules in argument
  order, so acquisition order IS the sorted order. ``test_shortest_group_goes
  _first`` is what catches a future refactor that gathers an unordered
  collection.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.layer4_frameworks.messaging.event_mesh_publisher_pump import (
    EventMeshPublisher,
    _SendItem,
)


def _item(name: str, seq: int, kind: str = "topic") -> _SendItem:
    return _SendItem(kind=kind, name=name, payload=str(seq).encode())


class _Pump:
    """A publisher with only the dispatch machinery wired.

    Built with ``__new__`` on purpose: the real constructor needs a token
    provider, HTTP clients, a DLQ spool and a circuit breaker, none of which
    take part in the decision under test. Wiring them would test the
    constructor, not the dispatcher.
    """

    def __init__(self, *, ceiling: int = 10,
                 latency: dict[str, float] | None = None,
                 fail_on: set[tuple[str, int]] | None = None) -> None:
        pub = EventMeshPublisher.__new__(EventMeshPublisher)
        pub._flush_concurrency = ceiling
        pub._metrics = MagicMock()
        pub._send_queue = MagicMock()          # only task_done() is reached
        self.sent: list[tuple[str, bytes]] = []
        self.done: list[tuple[str, bytes]] = []
        self.in_flight = 0
        self.peak_in_flight = 0
        self._latency = latency or {}
        self._fail = fail_on or set()
        seen: dict[str, int] = {}

        async def _tracked(item: _SendItem) -> None:
            nth = seen.get(item.name, 0)
            seen[item.name] = nth + 1
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
            try:
                await asyncio.sleep(self._latency.get(item.name, 0.0))
                self.sent.append((item.name, item.payload))
                if (item.name, nth) in self._fail:
                    raise RuntimeError(f"boom {item.name}#{nth}")
            finally:
                self.in_flight -= 1
                # Mirrors the real method: task_done() is in the finally, so
                # every item that starts owes exactly one, failure included.
                self.done.append((item.name, item.payload))

        pub._flush_one_tracked = _tracked      # type: ignore[method-assign]
        self.pub = pub

    def order(self, name: str) -> list[bytes]:
        return [p for n, p in self.sent if n == name]


class TestPerDestinationOrder:
    """The only ordering guarantee that survives to a consumer."""

    @pytest.mark.asyncio
    async def test_order_holds_within_a_batch_under_latency_skew(self):
        """A naive concurrent fan-out reorders here; a per-group one cannot.

        Without the latency skew this test passes for the WRONG reason — a
        broken implementation that gathers every item independently would
        still finish them in submission order when they all take 0 s.
        """
        p = _Pump(latency={"slow": 0.02, "fast": 0.001})
        batch = [_item("slow", 1), _item("fast", 1), _item("slow", 2),
                 _item("fast", 2), _item("slow", 3)]
        await p.pub._flush_batch(batch)
        assert p.order("slow") == [b"1", b"2", b"3"]
        assert p.order("fast") == [b"1", b"2"]

    @pytest.mark.asyncio
    async def test_order_holds_across_two_batches(self):
        """This is what the batch barrier buys.

        The caller awaits the whole batch before draining the next, so a
        destination's items in batch 2 cannot overtake its items in batch 1.
        Delete the barrier and this is the test that fails.
        """
        p = _Pump(latency={"slow": 0.02})
        await p.pub._flush_batch([_item("slow", 1), _item("other", 1)])
        await p.pub._flush_batch([_item("slow", 2)])
        assert p.order("slow") == [b"1", b"2"]

    @pytest.mark.asyncio
    async def test_a_topic_and_a_queue_sharing_a_name_are_not_merged(self):
        """``_flush_one`` builds ``/topics/…`` vs ``/queues/…`` — different
        destinations, so the group key must be (kind, name), not name."""
        p = _Pump()
        batch = [_item("dup", 1, kind="topic"), _item("dup", 2, kind="queue")]
        await p.pub._flush_batch(batch)
        assert p.peak_in_flight == 2, "same name, different kind — must not serialise"


class TestWidthIsDerivedNotConfigured:
    """A hardcoded width is wrong the first time an event type is added."""

    @staticmethod
    def _width(pump: _Pump) -> int:
        return pump.pub._metrics.record_flush_shape.call_args.kwargs["width"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("sizes", "expected"), [
        # Today on tenant-1: 19 items, realtime.events is 8 of them.
        ([8, 2, 2, 2, 1, 1, 1, 1, 1], 3),
        # After P1-6 removes the realtime mirror: 11 items, largest 2.
        ([2, 2, 2, 1, 1, 1, 1, 1], 6),
        # One destination must be serial anyway.
        ([5], 1),
        # Add three event types WITH their mirrors: the mirror pile grows to
        # 11 of 25, and the width stays at 3. The formula refuses to spend
        # connections on a problem concurrency cannot solve.
        ([11, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1], 3),
    ])
    async def test_width_follows_the_group_distribution(self, sizes, expected):
        p = _Pump(ceiling=100)
        batch = [_item(f"t{i}", n)
                 for i, size in enumerate(sizes) for n in range(size)]
        await p.pub._flush_batch(batch)
        assert self._width(p) == expected

    @pytest.mark.asyncio
    async def test_the_ceiling_clamps_the_pathological_case(self):
        """k singletons ask for k connections to save (k-1) POSTs. This is the
        one case where the ceiling's VALUE is load-bearing."""
        p = _Pump(ceiling=3)
        await p.pub._flush_batch([_item(f"t{i}", 0) for i in range(9)])
        assert self._width(p) == 3
        assert p.peak_in_flight <= 3

    @pytest.mark.asyncio
    async def test_the_shape_is_reported_with_every_label_every_time(self):
        """Prometheus freezes a label set on first use — a sample missing one
        key is dropped SILENTLY, which is how a metric ends up never
        appearing at all."""
        p = _Pump()
        await p.pub._flush_batch([_item("a", 1), _item("a", 2), _item("b", 1)])
        kwargs = p.pub._metrics.record_flush_shape.call_args.kwargs
        assert set(kwargs) == {"groups", "largest", "width", "batch"}
        assert kwargs["groups"] == 2 and kwargs["largest"] == 2
        assert kwargs["batch"] == 3


class TestShortestFirst:
    @pytest.mark.asyncio
    async def test_shortest_group_goes_first(self):
        """The run-advancing group has the LATEST first item in a node cycle,
        so insertion order would dispatch it last — behind the 8-item
        telemetry group, giving back most of what this change is for.

        Also the guard on FIFO-fair acquisition: gather schedules in argument
        order, so a refactor that gathers an unordered collection breaks here
        and nowhere else.
        """
        p = _Pump(ceiling=1, latency={"big": 0.005, "small": 0.005})
        batch = [_item("big", n) for n in range(5)] + [_item("small", 0)]
        await p.pub._flush_batch(batch)
        assert p.sent[0][0] == "small", "small group must not queue behind the big one"


class TestNothingIsStranded:
    @pytest.mark.asyncio
    async def test_a_failure_mid_group_still_finishes_the_group(self):
        """Every item owes the send queue exactly one ``task_done()`` and
        ``stop()`` blocks on ``join()``. An exception escaping mid-group
        strands the tail's accounting and hangs shutdown — silently, and only
        at shutdown, which is the worst place to find it."""
        p = _Pump(fail_on={("t", 1)})
        await p.pub._flush_batch([_item("t", n) for n in range(4)])
        assert len(p.done) == 4, "every item must be accounted for, failure included"
        assert p.order("t") == [b"0", b"1", b"2", b"3"]

    @pytest.mark.asyncio
    async def test_one_failing_group_does_not_kill_the_others(self):
        p = _Pump(fail_on={("bad", 0)})
        await p.pub._flush_batch([_item("bad", 0), _item("good", 0)])
        assert len(p.done) == 2


class TestTheShapeIsAHistogramNotAGauge:
    """F13: these started as gauges and cost a live A/B.

    Sampled after a probe finished, all four read 1.0 — which looks exactly
    like "batches are size 1, so grouping had nothing to group" and is really
    "the last flush while idle was one message". A gauge read at idle only
    tells you about idle. The distribution during a run is the whole question,
    so it has to be a histogram.
    """

    def test_flush_shape_is_recorded_as_a_distribution(self):
        from app.layer4_frameworks.messaging.event_mesh_metrics import (
            EventMeshMetrics,
        )
        monitor = MagicMock()
        EventMeshMetrics(monitor).record_flush_shape(
            groups=4, largest=8, width=3, batch=19,
        )
        assert monitor.histogram.call_count == 4
        monitor.track.assert_not_called()
        recorded = {c.args[0]: c.args[1] for c in monitor.histogram.call_args_list}
        assert recorded == {
            "eventmesh.flush.groups": 4,
            "eventmesh.flush.largest_group": 8,
            "eventmesh.flush.width": 3,
            "eventmesh.flush.batch_size": 19,
        }

    def test_a_monitor_that_cannot_do_histograms_never_breaks_publishing(self):
        """Observability must never take down the publish path."""
        from app.layer4_frameworks.messaging.event_mesh_metrics import (
            EventMeshMetrics,
        )
        monitor = MagicMock()
        monitor.histogram.side_effect = RuntimeError("no histograms here")
        EventMeshMetrics(monitor).record_flush_shape(
            groups=1, largest=1, width=1, batch=1,
        )
