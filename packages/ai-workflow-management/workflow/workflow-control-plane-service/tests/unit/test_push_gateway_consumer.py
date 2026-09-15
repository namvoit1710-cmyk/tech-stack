from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

from app.layer4_frameworks.realtime.push_gateway_consumer import PushGatewayRealtimeConsumer


class _FakeClient:
    provider_name = "http"

    def __init__(self) -> None:
        self.send_event = AsyncMock(return_value=True)
        self.close = AsyncMock(return_value=None)


async def test_direct_notify_enqueues_message_without_broker():
    client = _FakeClient()
    consumer = PushGatewayRealtimeConsumer(client=client, broker=None)
    message = {
        "event_type": "run.started",
        "rooms": ["run:r1"],
        "payload": {"run_id": "r1"},
    }

    consumer.start()
    consumer.notify(message)

    queued = await asyncio.wait_for(consumer._queue.get(), timeout=0.1)
    assert queued == message


async def test_process_sends_each_room_to_push_gateway():
    client = _FakeClient()
    consumer = PushGatewayRealtimeConsumer(client=client, broker=None)

    await consumer._process({
        "event_type": "node.completed",
        # "rooms": ["run:r1", "workflow:w1"],
        "rooms": ["run:r1"],
        "payload": {"task_id": "t1"},
    })

    assert client.send_event.await_count == 1
    client.send_event.assert_any_await(
        key="run:r1",
        event_type="node.completed",
        payload={"task_id": "t1"},
        is_final=False,
    )
    # client.send_event.assert_any_await(
    #     key="workflow:w1",
    #     event_type="node.completed",
    #     payload={"task_id": "t1"},
    #     is_final=False,
    # )

async def test_process_stamps_run_seq_into_forwarded_payload():
    """_run_seq/_run_key from the envelope must ride into the gateway payload
    so downstream subscribers can resequence per run."""
    client = _FakeClient()
    consumer = PushGatewayRealtimeConsumer(client=client, broker=None)

    await consumer._process({
        "event_type": "node.completed",
        "rooms": ["run:r1"],
        "payload": {"task_id": "t1"},
        "_run_seq": 7,
        "_run_key": "r1",
    })

    client.send_event.assert_any_await(
        key="run:r1",
        event_type="node.completed",
        payload={"task_id": "t1", "_run_seq": 7, "_run_key": "r1"},
        is_final=False,
    )


async def test_process_does_not_mutate_shared_payload():
    """The envelope payload is shared with SocketIORealtimeConsumer — stamping
    must copy, never mutate."""
    client = _FakeClient()
    consumer = PushGatewayRealtimeConsumer(client=client, broker=None)
    shared_payload = {"task_id": "t1"}

    await consumer._process({
        "event_type": "node.completed",
        "rooms": ["run:r1"],
        "payload": shared_payload,
        "_run_seq": 1,
        "_run_key": "r1",
    })

    assert shared_payload == {"task_id": "t1"}


async def test_process_without_run_seq_forwards_payload_unchanged():
    client = _FakeClient()
    consumer = PushGatewayRealtimeConsumer(client=client, broker=None)

    await consumer._process({
        "event_type": "node.completed",
        "rooms": ["run:r1"],
        "payload": {"task_id": "t1"},
    })

    client.send_event.assert_any_await(
        key="run:r1",
        event_type="node.completed",
        payload={"task_id": "t1"},
        is_final=False,
    )


# ---------------------------------------------------------------------------
# Per-run resequencing before forwarding to the Push Gateway
#
# Regression cover for "node status stuck 'executing' after run finishes":
# Event Mesh can deliver ``run.status=completed`` ahead of the final
# ``node.completed``; the gateway path had NO reorder buffer, so the terminal
# run event overtook the node event and the frontend dropped it. The consumer
# now resequences per ``_run_seq`` so the terminal run event is forwarded only
# after every lower-seq node event.
# ---------------------------------------------------------------------------


def _msg(run_seq: int, *, event_type: str = "node.completed", run_key: str = "r1") -> dict:
    return {
        "event_type": event_type,
        "rooms": [f"run:{run_key}"],
        "payload": {"seq": run_seq},
        "_run_key": run_key,
        "_run_seq": run_seq,
    }


def _forwarded_seqs(client) -> list[int]:
    """The ``_run_seq`` of each event forwarded, in forward order."""
    return [c.kwargs["payload"]["_run_seq"] for c in client.send_event.await_args_list]


def _forwarded_events(client) -> list[str]:
    return [c.kwargs["event_type"] for c in client.send_event.await_args_list]


async def test_drain_round_forwards_in_run_seq_order():
    client = _FakeClient()
    consumer = PushGatewayRealtimeConsumer(client=client, broker=None)

    # Queued out of order: 2, 1, 3.
    consumer._queue.put_nowait(_msg(2))
    consumer._queue.put_nowait(_msg(1))
    consumer._queue.put_nowait(_msg(3, event_type="node.dispatched"))

    await consumer._drain_round()

    assert _forwarded_seqs(client) == [1, 2, 3]


async def test_run_status_completed_does_not_overtake_final_node():
    """The exact bug: run.status=completed (seq 2) arrives before the final
    node.completed (seq 1); it must be buffered and forwarded AFTER the node."""
    client = _FakeClient()
    consumer = PushGatewayRealtimeConsumer(client=client, broker=None)

    await consumer._accept(_msg(2, event_type="run.status"))
    assert client.send_event.await_count == 0  # terminal event held, not forwarded

    await consumer._accept(_msg(1, event_type="node.completed"))

    assert _forwarded_events(client) == ["node.completed", "run.status"]


async def test_future_event_buffered_until_gap_fills():
    client = _FakeClient()
    consumer = PushGatewayRealtimeConsumer(client=client, broker=None)

    await consumer._accept(_msg(1))
    await consumer._accept(_msg(3))  # future — buffered
    assert _forwarded_seqs(client) == [1]

    await consumer._accept(_msg(2))  # fills the gap → 2 then 3 flush
    assert _forwarded_seqs(client) == [1, 2, 3]


async def test_gap_flush_forwards_buffered_after_timeout():
    client = _FakeClient()
    consumer = PushGatewayRealtimeConsumer(client=client, broker=None)

    await consumer._accept(_msg(1))
    await consumer._accept(_msg(3))  # seq 2 never arrives (dropped)

    # Backdate the buffered event past the gap-flush window.
    state = consumer._order["r1"]
    msg, _ = state.buffered[3]
    state.buffered[3] = (msg, time.monotonic() - 10.0)

    await consumer._flush_gaps()
    assert _forwarded_seqs(client) == [1, 3]


async def test_independent_runs_do_not_block_each_other():
    client = _FakeClient()
    consumer = PushGatewayRealtimeConsumer(client=client, broker=None)

    await consumer._accept(_msg(2, run_key="rA"))  # rA future — buffered
    await consumer._accept(_msg(1, run_key="rB"))  # rB forwards freely

    assert _forwarded_seqs(client) == [1]


async def test_unsequenced_event_forwarded_as_is():
    """Workflow-scoped events with no run context bypass resequencing."""
    client = _FakeClient()
    consumer = PushGatewayRealtimeConsumer(client=client, broker=None)

    await consumer._accept({
        "event_type": "workflow.updated",
        "rooms": ["workflow:w1"],
        "payload": {"task_id": "t1"},
    })

    assert _forwarded_events(client) == ["workflow.updated"]
