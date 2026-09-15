"""Batch drain on the REST poll loop (2026-08-05).

The loop took exactly ONE message per queue per pass, so a burst arriving
together was spread over successive passes — each message paying a poll
interval before the next was fetched. Measured on tenant-1: the engine
enqueues one ``ExecuteNodeAction`` per ready task correctly, but two sibling
actions published milliseconds apart were dispatched 13.5s and 15.8s apart.

Draining changes throughput, not semantics: still sequential, still per-queue
FIFO, still one ack per message after its handler succeeded.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig
from app.layer4_frameworks.messaging.event_mesh_consumer import EventMeshConsumer
from app.layer4_frameworks.messaging.event_mesh_names import EventMeshNames


def _make_consumer(**overrides) -> EventMeshConsumer:
    defaults = dict(
        messaging_url="https://messaging.example.com",
        names=EventMeshNames("ns"),
        tokens=MagicMock(),
        admin=MagicMock(),
        clients=MagicMock(),
        poll_interval_s=0.05,
        lock_duration_ms=60_000,
        handler_timeout_s=None,
        consume_transport="rest",
        amqp_host="",
        amqp_port=443,
        amqp_ws_path="/protocols/amqp10ws",
        amqp_prefetch=50,
        amqp_concurrency=1,
        amqp_strip_namespace=False,
        metrics=MagicMock(),
    )
    defaults.update(overrides)
    return EventMeshConsumer(**defaults)


def _msgs(n: int) -> list:
    """n messages then an empty queue."""
    return [{"data": {"i": i}, "message_id": f"m{i}"} for i in range(n)] + [None]


class TestDrain:
    def test_drains_a_burst_in_one_pass(self):
        """Five messages waiting must not need five poll passes."""
        consumer = _make_consumer(batch_max=10)
        consumer.consume_from_queue = MagicMock(side_effect=_msgs(5))
        consumer.acknowledge = MagicMock()
        consumer._dispatch_to_callbacks = MagicMock(return_value=True)

        handled = consumer._drain_queue("q")

        assert handled == 5
        assert consumer._dispatch_to_callbacks.call_count == 5
        assert consumer.acknowledge.call_count == 5, "one ack per message"
        consumer.close()

    def test_batch_max_one_restores_single_message_behaviour(self):
        """The knob must be able to reproduce the old loop exactly."""
        consumer = _make_consumer(batch_max=1)
        consumer.consume_from_queue = MagicMock(side_effect=_msgs(5))
        consumer.acknowledge = MagicMock()
        consumer._dispatch_to_callbacks = MagicMock(return_value=True)

        assert consumer._drain_queue("q") == 1
        assert consumer.consume_from_queue.call_count == 1
        consumer.close()

    def test_stops_at_batch_max(self):
        consumer = _make_consumer(batch_max=3)
        consumer.consume_from_queue = MagicMock(side_effect=_msgs(10))
        consumer.acknowledge = MagicMock()
        consumer._dispatch_to_callbacks = MagicMock(return_value=True)

        assert consumer._drain_queue("q") == 3
        consumer.close()

    def test_stops_when_queue_empties(self):
        consumer = _make_consumer(batch_max=10)
        consumer.consume_from_queue = MagicMock(side_effect=_msgs(2))
        consumer.acknowledge = MagicMock()
        consumer._dispatch_to_callbacks = MagicMock(return_value=True)

        assert consumer._drain_queue("q") == 2
        consumer.close()

    def test_handler_failure_skips_ack_and_stops_the_drain(self):
        """Unacked message redelivers; do not pile onto a failing consumer."""
        consumer = _make_consumer(batch_max=10)
        consumer.consume_from_queue = MagicMock(side_effect=_msgs(5))
        consumer.acknowledge = MagicMock()
        consumer._dispatch_to_callbacks = MagicMock(return_value=False)

        handled = consumer._drain_queue("q")

        assert handled == 1
        consumer.acknowledge.assert_not_called(), "no ack on handler failure"
        consumer.close()

    def test_poll_error_stops_the_drain(self):
        consumer = _make_consumer(batch_max=10)
        consumer.consume_from_queue = MagicMock(side_effect=RuntimeError("boom"))
        consumer._dispatch_to_callbacks = MagicMock(return_value=True)

        assert consumer._drain_queue("q") == 0
        consumer.close()

    def test_cooldown_armed_mid_drain_stops_it(self):
        """A 429 during the batch must pause immediately, not finish the batch."""
        consumer = _make_consumer(batch_max=10)
        consumer.consume_from_queue = MagicMock(side_effect=_msgs(5))
        consumer.acknowledge = MagicMock()
        consumer._dispatch_to_callbacks = MagicMock(return_value=True)
        calls = {"n": 0}

        def _cooldown(_queue: str) -> float:
            calls["n"] += 1
            return 0.0 if calls["n"] <= 2 else 5.0  # armed after 2 messages

        consumer._cooldown_remaining = _cooldown

        assert consumer._drain_queue("q") == 2
        consumer.close()


class TestConfig:
    @staticmethod
    def _cfg(**overrides) -> EventMeshConfig:
        return EventMeshConfig(
            token_url="https://t", messaging_url="https://m",
            management_url="https://mg", client_id="c", client_secret="s",
            **overrides,
        )

    def test_config_default_and_clamp(self):
        assert self._cfg().consume_batch_max == 10
        assert self._cfg(consume_batch_max=0).consume_batch_max == 1
        assert self._cfg(consume_batch_max=-5).consume_batch_max == 1


class TestNoHandlerIsNotDataLoss:
    """A message dispatched to a queue with no registered handler is NOT acked.

    `_dispatch_to_callbacks` starts `all_ok = True` and loops over the handler
    list; an empty list used to mean the loop body never ran, True was
    returned, and the caller acked — destroying the message. Until 2026-08-05
    it left no trace either, which is exactly the shape of the tenant-1 AMQP
    failure (links open, no errors, work vanishing).

    Measured on tenant-1 2026-08-05 (`solace-971`): six `orchestration.actions`
    messages destroyed on a single startup, because the AMQP branch of
    `subscribe` attached the link before registering the callback. On that
    queue a discarded StartRunAction is a permanently hung run.

    So the contract changed: leave it unsettled. The 60s lock expires, the
    broker redelivers, and a racing handler now exists. A genuine key mismatch
    exhausts maxRedeliveryCount and lands in the DMQ — alertable, recoverable,
    and strictly better than destruction.
    """

    def test_unknown_queue_is_not_acked(self, caplog):
        import logging
        consumer = _make_consumer()
        consumer._callbacks = {"ns/known": [lambda _d: None]}

        with caplog.at_level(logging.WARNING):
            result = consumer._dispatch_to_callbacks("ns/unknown", {"x": 1})

        assert result is False, "acking here destroys the message — redeliver instead"
        assert any("No handler registered" in r.message for r in caplog.records), \
            "an undeliverable message must never be silent"
        assert any("ns/known" in str(r.args) or "ns/known" in r.getMessage()
                   for r in caplog.records), "the log must show the registered keys"
        consumer.close()

    def test_known_queue_still_runs_its_handler(self):
        seen = []
        consumer = _make_consumer()
        consumer._callbacks = {"ns/known": [lambda d: seen.append(d)]}

        assert consumer._dispatch_to_callbacks("ns/known", {"x": 1}) is True
        assert seen == [{"x": 1}]
        consumer.close()
