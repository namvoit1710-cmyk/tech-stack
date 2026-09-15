"""The action backlog is bounded, and a full one pushes back on the broker.

``BrokerOrchestrationQueue`` bridges the broker's consumer thread onto the main
event loop with ``put_nowait`` and returns immediately — deliberately, because
blocking there is what made one slow ``complete_task`` stall every other queue
(SA-2319). The cost is that the message is ACKED before the action has run.

With an unbounded queue that gave two failures, both worse the more throughput
the pod is given:

* a burst grew the backlog without limit inside a 1 GB pod;
* every entry was already acked, so a crash lost them with no redelivery.

Bounding it turns both into broker backpressure. The subtlety this file exists
to pin: refusal must RAISE. ``EventMeshConsumer._dispatch_to_callbacks`` ignores
a sync handler's return value and marks the message failed only on an
exception — and only a failed message is left unsettled for redelivery.
Returning False, or dropping, would silently destroy a ``StartRunAction``,
which is a permanently hung run.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.layer2_application.interfaces.orchestration_queue_interface import (
    StartRunAction,
)
from app.layer4_frameworks.orchestration_queue import kafka_queue as kq
from app.layer4_frameworks.orchestration_queue.kafka_queue import (
    BacklogFull,
    BrokerOrchestrationQueue,
    orchestration_backlog_maxsize,
)


def _queue(monkeypatch, maxsize: int | None = None) -> BrokerOrchestrationQueue:
    if maxsize is not None:
        monkeypatch.setenv("ORCHESTRATION_BACKLOG_MAX", str(maxsize))
        monkeypatch.setattr(
            kq, "orchestration_backlog_maxsize", lambda: maxsize,
        )
    return BrokerOrchestrationQueue(MagicMock())


def _capture_on_message(q: BrokerOrchestrationQueue):
    """Subscribe and hand back the callback the broker would invoke."""
    captured = {}
    q._broker.subscribe_to_topic.side_effect = (
        lambda _topic, cb: captured.setdefault("cb", cb)
    )
    q.start_consumer()
    return captured["cb"]


def _msg(run_id: str = "run-1") -> dict:
    from dataclasses import asdict
    return asdict(StartRunAction(run_id=run_id))


class TestTheBacklogIsBounded:
    def test_it_has_a_maxsize_at_all(self, monkeypatch):
        q = _queue(monkeypatch)
        assert q._internal.maxsize > 0, "an unbounded backlog is the bug"

    def test_the_default_derives_from_the_memory_budget(self):
        assert orchestration_backlog_maxsize() == (
            kq._BACKLOG_BUDGET_BYTES // kq._ACTION_SIZE_ESTIMATE_BYTES
        )

    def test_an_explicit_override_wins(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATION_BACKLOG_MAX", "7")
        assert orchestration_backlog_maxsize() == 7

    def test_a_garbage_override_falls_back_instead_of_crashing(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATION_BACKLOG_MAX", "lots")
        assert orchestration_backlog_maxsize() > 0


class TestAFullBacklogPushesBack:
    @pytest.mark.asyncio
    async def test_it_raises_so_the_message_is_not_acked(self, monkeypatch):
        """Return-False would be silently ignored and the message acked."""
        q = _queue(monkeypatch, maxsize=1)
        on_message = _capture_on_message(q)  # captures the running loop

        on_message(_msg("run-1"))
        await asyncio.sleep(0)          # let call_soon_threadsafe land
        assert q._internal.qsize() == 1

        with pytest.raises(BacklogFull):
            on_message(_msg("run-2"))

    @pytest.mark.asyncio
    async def test_the_refused_action_is_not_silently_dropped(self, monkeypatch):
        """A dropped StartRunAction is a permanently hung run — the exact
        failure the AMQP workstream exists to prevent."""
        q = _queue(monkeypatch, maxsize=1)
        on_message = _capture_on_message(q)  # captures the running loop

        on_message(_msg("run-1"))
        await asyncio.sleep(0)
        with pytest.raises(BacklogFull):
            on_message(_msg("run-2"))
        await asyncio.sleep(0)

        assert q._internal.qsize() == 1
        assert (await q.dequeue()).run_id == "run-1"

    @pytest.mark.asyncio
    async def test_room_frees_up_and_the_redelivery_lands(self, monkeypatch):
        q = _queue(monkeypatch, maxsize=1)
        on_message = _capture_on_message(q)  # captures the running loop

        on_message(_msg("run-1"))
        await asyncio.sleep(0)
        with pytest.raises(BacklogFull):
            on_message(_msg("run-2"))

        await q.dequeue()               # a worker drains one
        on_message(_msg("run-2"))       # the broker's redelivery
        await asyncio.sleep(0)
        assert (await q.dequeue()).run_id == "run-2"

    @pytest.mark.asyncio
    async def test_refusals_are_counted(self, monkeypatch):
        q = _queue(monkeypatch, maxsize=1)
        on_message = _capture_on_message(q)  # captures the running loop

        on_message(_msg("run-1"))
        await asyncio.sleep(0)
        for _ in range(3):
            with pytest.raises(BacklogFull):
                on_message(_msg("run-x"))
        assert q.backlog_stats.refused == 3


class TestItIsObservable:
    def test_the_snapshot_reports_depth_and_ceiling(self, monkeypatch):
        q = _queue(monkeypatch, maxsize=5)
        snap = q.backlog_stats.budget_snapshot()
        assert snap["entries"] == 0
        assert snap["maxsize"] == 5
        assert snap["refused"] == 0

    def test_the_stats_object_is_weak_referenceable(self, monkeypatch):
        """The budget registry holds members weakly and registration is inside
        a try/except, so __slots__ without __weakref__ fails SILENTLY and the
        metric simply never appears. Twice before in SA-2254."""
        import weakref
        q = _queue(monkeypatch)
        assert weakref.ref(q.backlog_stats) is not None
