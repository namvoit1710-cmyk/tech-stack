"""SA-1518 — prove per-run ordering with TWO control-plane instances
competing on the realtime stream.

With 2 CPs, `sub-realtime-events` is a competing-consumer queue: each
envelope lands on exactly ONE instance, so each instance's reorder buffer
sees permanent gaps (the peer's events). At-least-once delivery adds
redelivered duplicates and out-of-order arrivals on top.

**This suite was written against the Socket.IO consumer, which has been
removed now that every environment delivers realtime through the Push
Gateway. The scenario did not go away with it** — the Push Gateway consumer
subscribes to the same shared `realtime.events` queue and would be split the
same way by a second instance. So the guarantees are re-proved here against
the sink that is actually live.

The guarantees this suite PROVES (and the runbook documents):
1. Zero loss — the union of both instances' emissions covers every seq;
   gap-flush emits rather than drops, late events emit rather than drop.
2. Bounded stall — a gap (peer-consumed or lost predecessor) delays a
   buffered event at most ``_GAP_FLUSH_SECONDS``; a run never stalls.
3. Within-instance order — each instance emits its own subset in
   ascending seq order (in-order path + seq-sorted gap-flush).
4. Duplicates are emitted, not dropped (at-least-once surfaces them;
   dedupe is SA-1375's projector — documented limit, not silent loss).

NOT guaranteed (documented limit): cross-instance TOTAL order for a
single client — that requires sticky per-run routing or a gateway-side
merge; see docs/runbooks/two-cp-realtime-ordering.md.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.layer4_frameworks.realtime import push_gateway_consumer as pg_mod
from app.layer4_frameworks.realtime.push_gateway_consumer import (
    PushGatewayRealtimeConsumer,
)


class _FakeGateway:
    """Captures forwarded events in arrival order."""

    provider_name = "fake"

    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict, str]] = []

    async def send_event(self, key, event_type, payload, is_final=False):
        self.emitted.append((event_type, payload, key))
        return True

    async def close(self):
        return None


def _envelope(seq: int, run: str = "run-1") -> dict:
    return {
        "event_type": "node.completed",
        "rooms": [f"run:{run}"],
        "payload": {"seq": seq},
        "_run_key": run,
        "_run_seq": seq,
    }


def _seqs(gw: _FakeGateway) -> list[int]:
    return [p["seq"] for _, p, _ in gw.emitted]


def _make_cp() -> tuple[PushGatewayRealtimeConsumer, _FakeGateway]:
    gw = _FakeGateway()
    return PushGatewayRealtimeConsumer(client=gw, broker=None), gw


@pytest.fixture()
def fast_gap_window(monkeypatch):
    """Shrink the gap-flush window so tests run in milliseconds."""
    monkeypatch.setattr(pg_mod, "_GAP_FLUSH_SECONDS", 0.05)


class TestTwoCpCompetingConsumption:
    @pytest.mark.asyncio
    async def test_interleaved_split_zero_loss_union(self, fast_gap_window):
        """Competing consumers: A gets odd seqs, B gets even seqs (worst-case
        interleave). Union of emissions must equal the full stream."""
        cp_a, gw_a = _make_cp()
        cp_b, gw_b = _make_cp()

        n = 20
        for seq in range(1, n + 1):
            target = cp_a if seq % 2 else cp_b
            await target._accept(_envelope(seq))

        await asyncio.sleep(0.06)  # exceed the (patched) gap window
        await cp_a._flush_gaps()
        await cp_b._flush_gaps()

        union = sorted(_seqs(gw_a) + _seqs(gw_b))
        assert union == list(range(1, n + 1))  # ZERO loss (AC)

    @pytest.mark.asyncio
    async def test_each_instance_emits_its_subset_in_order(self, fast_gap_window):
        cp_a, gw_a = _make_cp()
        cp_b, gw_b = _make_cp()

        for seq in range(1, 21):
            target = cp_a if seq % 2 else cp_b
            await target._accept(_envelope(seq))
        await asyncio.sleep(0.06)
        await cp_a._flush_gaps()
        await cp_b._flush_gaps()

        assert _seqs(gw_a) == sorted(_seqs(gw_a))  # within-instance order
        assert _seqs(gw_b) == sorted(_seqs(gw_b))

    @pytest.mark.asyncio
    async def test_gap_stall_is_bounded_not_forever(self, fast_gap_window):
        """A buffered event whose predecessor went to the peer must emit
        after the gap window — the run never stalls."""
        cp_a, gw_a = _make_cp()
        # seq 1 went to the peer; A receives only seq 2.
        await cp_a._accept(_envelope(2))
        assert _seqs(gw_a) == []  # buffered, waiting for seq 1

        start = time.monotonic()
        while not gw_a.emitted and time.monotonic() - start < 2.0:
            await asyncio.sleep(0.02)
            await cp_a._flush_gaps()

        assert _seqs(gw_a) == [2]  # emitted, bounded by the window
        assert time.monotonic() - start < 1.0

    @pytest.mark.asyncio
    async def test_redelivered_duplicate_is_emitted_not_dropped(self, fast_gap_window):
        """At-least-once redelivery: the duplicate emits late (never lost);
        dedupe is the SA-1375 projector's job — documented, not silent."""
        cp_a, gw_a = _make_cp()
        await cp_a._accept(_envelope(1))
        await cp_a._accept(_envelope(2))
        await cp_a._accept(_envelope(1))  # lock-TTL redelivery
        assert _seqs(gw_a) == [1, 2, 1]

    @pytest.mark.asyncio
    async def test_out_of_order_arrival_within_instance_is_resequenced(self):
        """Redelivery reorder INSIDE one instance still yields 1,2,3."""
        cp_a, gw_a = _make_cp()
        await cp_a._accept(_envelope(3))
        await cp_a._accept(_envelope(1))
        await cp_a._accept(_envelope(2))
        assert _seqs(gw_a) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_two_runs_resequence_independently(self, fast_gap_window):
        cp_a, gw_a = _make_cp()
        await cp_a._accept(_envelope(1, run="r1"))
        await cp_a._accept(_envelope(2, run="r2"))  # r2 waits for its seq 1
        await cp_a._accept(_envelope(2, run="r1"))
        await cp_a._accept(_envelope(1, run="r2"))
        emitted = [(p["seq"], room) for _, p, room in gw_a.emitted]
        assert emitted == [
            (1, "run:r1"), (2, "run:r1"), (1, "run:r2"), (2, "run:r2"),
        ]

    @pytest.mark.asyncio
    async def test_full_chaos_mix_converges(self, fast_gap_window):
        """The AC scenario in one test: interleave across 2 CPs + one
        redelivered duplicate + out-of-order arrival — union complete,
        per-instance monotone (modulo the flagged late duplicate)."""
        cp_a, gw_a = _make_cp()
        cp_b, gw_b = _make_cp()

        # seqs 1..10, odd→A even→B, but A's 3 arrives before A's 1,
        # and B redelivers 4 a second time.
        for seq in (3, 1, 5, 7, 9):
            await cp_a._accept(_envelope(seq))
        for seq in (2, 4, 6, 4, 8, 10):
            await cp_b._accept(_envelope(seq))

        await asyncio.sleep(0.06)
        await cp_a._flush_gaps()
        await cp_b._flush_gaps()

        union = sorted(set(_seqs(gw_a) + _seqs(gw_b)))
        assert union == list(range(1, 11))  # nothing lost
        # A duplicate redelivered while its seq is STILL BUFFERED coalesces
        # (dict slot overwrite) — emitted exactly once, never lost. The
        # emitted-after-slot duplicate case (emits late, twice) is proven by
        # test_redelivered_duplicate_is_emitted_not_dropped above.
        assert _seqs(gw_b).count(4) == 1
