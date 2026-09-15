"""SA-2319 — a blocking handler on one queue must not stall the others.

The REST consumer used to run ONE thread that round-robined every subscribed
queue and called each handler inline. ``WorkerResultsConsumer`` registers a
*sync* callback that waits on the whole of ``complete_task``, so for the entire
duration of a task completion the thread never reached ``orchestration.actions``:

    poll thread   [results POST][==== complete_task, 200-400ms ====][actions POST]
    actions queue               ....ContinueRunAction sits unread....
    workers 0-3                 ..........idle, nothing to dequeue........

The four ``OrchestrationWorker`` coroutines starved behind the very message
meant to feed them, and realtime / triggers / catalog / the response topic
waited on a completion they have nothing to do with.

The first test below is the whole ticket: it fails against the old single-thread
loop and passes with per-queue threads. The rest guard the things that must NOT
have changed on the way — above all the ack contract, because weakening
at-least-once here would lose task results silently.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from app.layer4_frameworks.messaging.event_mesh_consumer import EventMeshConsumer
from app.layer4_frameworks.messaging.event_mesh_names import EventMeshNames

RESULTS = "simplemdg/event/ns/workflow.results"
ACTIONS = "simplemdg/event/ns/orchestration.actions"


def _consumer(**overrides) -> EventMeshConsumer:
    metrics = overrides.pop("metrics", MagicMock())
    defaults = dict(
        messaging_url="https://messaging.example.com",
        names=EventMeshNames("ns"),
        tokens=MagicMock(),
        admin=MagicMock(),
        clients=MagicMock(),
        poll_interval_s=0.01,
        lock_duration_ms=60_000,
        handler_timeout_s=None,
        consume_transport="rest",
        amqp_host="",
        amqp_port=443,
        amqp_ws_path="/protocols/amqp10ws",
        amqp_prefetch=50,
        amqp_concurrency=1,
        amqp_strip_namespace=False,
        metrics=metrics,
    )
    defaults.update(overrides)
    return EventMeshConsumer(**defaults)


def _run_threads(consumer: EventMeshConsumer, queues, duration_s: float) -> None:
    """Drive ``_poll_loop`` for *queues* concurrently, then stop them."""
    consumer._running = True
    threads = [
        threading.Thread(target=consumer._poll_loop, args=(q,), daemon=True)
        for q in queues
    ]
    for t in threads:
        t.start()
    time.sleep(duration_s)
    consumer._running = False
    for t in threads:
        t.join(timeout=2)


class TestASlowHandlerDoesNotStallOtherQueues:
    def test_actions_keeps_being_consumed_during_a_long_completion(self):
        """The regression, stated directly.

        ``workflow.results`` gets one message whose handler blocks — a task
        completion. ``orchestration.actions`` has work waiting the whole time.
        Under the old shared loop the actions queue was consumed ZERO times
        during that window, because the single thread was inside the results
        handler. It must now be consumed repeatedly.

        Deliberately driven through ``_start_poll_loop`` — the entry point
        ``subscribe`` uses — and NOT by spawning the threads here. Spawning
        them would prove only that ``_poll_loop`` can run concurrently, which
        was never in doubt; it is the consumer's own choice of how many threads
        to start that this ticket changed, so that choice is what the test has
        to exercise. A revert to one shared thread has to fail here.
        """
        consumer = _consumer()
        in_handler = threading.Event()
        release = threading.Event()
        actions_polls = []

        def _consume(queue_name):
            if queue_name == RESULTS:
                return {"data": {"v": 1}, "message_id": "m-1"}
            actions_polls.append(time.monotonic())
            return None

        def _slow_results_handler(_data):
            in_handler.set()
            release.wait(timeout=2)

        consumer.consume_from_queue = MagicMock(side_effect=_consume)
        consumer.acknowledge = MagicMock()
        consumer._callbacks[RESULTS] = [_slow_results_handler]
        consumer._callbacks[ACTIONS] = [lambda d: None]

        consumer._start_poll_loop(RESULTS)
        consumer._start_poll_loop(ACTIONS)
        try:
            assert in_handler.wait(timeout=2), "results handler never ran"
            polls_at_entry = len(actions_polls)
            time.sleep(0.3)          # the completion is still in flight
            polls_during = len(actions_polls) - polls_at_entry
        finally:
            release.set()
            consumer.close()

        assert polls_during > 0, (
            "orchestration.actions was not polled once while a complete_task "
            "handler was running — the convoy is back, and the four "
            "OrchestrationWorker coroutines are starving behind it"
        )

    def test_each_queue_gets_its_own_named_thread(self):
        consumer = _consumer()
        consumer.consume_from_queue = MagicMock(return_value=None)
        consumer._callbacks[RESULTS] = [lambda d: None]
        consumer._callbacks[ACTIONS] = [lambda d: None]

        consumer._start_poll_loop(RESULTS)
        consumer._start_poll_loop(ACTIONS)
        try:
            assert set(consumer._consumer_threads) == {RESULTS, ACTIONS}
            names = {t.name for t in consumer._consumer_threads.values()}
            assert names == {
                f"event-mesh-consumer-{RESULTS}",
                f"event-mesh-consumer-{ACTIONS}",
            }
            assert all(t.daemon for t in consumer._consumer_threads.values())
        finally:
            consumer.close()

    def test_a_second_subscribe_to_one_queue_does_not_start_a_second_thread(self):
        """Two callbacks can share a queue — ``realtime.events`` has exactly
        that (socketio + push gateway). A duplicate thread would consume the
        same queue twice and each message would reach only one of them."""
        consumer = _consumer()
        consumer.consume_from_queue = MagicMock(return_value=None)
        consumer._callbacks[RESULTS] = [lambda d: None]

        consumer._start_poll_loop(RESULTS)
        first = consumer._consumer_threads[RESULTS]
        consumer._start_poll_loop(RESULTS)
        try:
            assert len(consumer._consumer_threads) == 1
            assert consumer._consumer_threads[RESULTS] is first
        finally:
            consumer.close()

    def test_close_joins_every_thread(self):
        consumer = _consumer()
        consumer.consume_from_queue = MagicMock(return_value=None)
        consumer._callbacks[RESULTS] = [lambda d: None]
        consumer._callbacks[ACTIONS] = [lambda d: None]
        consumer._start_poll_loop(RESULTS)
        consumer._start_poll_loop(ACTIONS)
        started = list(consumer._consumer_threads.values())

        consumer.close()

        assert consumer._consumer_threads == {}
        assert not any(t.is_alive() for t in started)


class TestAtLeastOnceIsUnchanged:
    """The ack contract is the thing this change must not touch. It is what
    makes a failed completion redeliver instead of vanishing."""

    def test_ack_after_a_successful_handler(self):
        consumer = _consumer()
        acked = []
        consumer.consume_from_queue = MagicMock(
            side_effect=[{"data": {"v": 1}, "message_id": "m-1"}, None, None],
        )
        consumer.acknowledge = MagicMock(side_effect=lambda q, m: acked.append((q, m)))
        consumer._callbacks[RESULTS] = [lambda d: None]

        _run_threads(consumer, [RESULTS], 0.12)

        assert acked == [(RESULTS, "m-1")]

    def test_no_ack_when_the_handler_raises(self):
        consumer = _consumer()
        consumer.consume_from_queue = MagicMock(
            side_effect=[{"data": {"v": 1}, "message_id": "m-1"}, None, None],
        )
        consumer.acknowledge = MagicMock()

        def _boom(_data):
            raise RuntimeError("handler failed")

        consumer._callbacks[RESULTS] = [_boom]

        _run_threads(consumer, [RESULTS], 0.12)

        consumer.acknowledge.assert_not_called()

    def test_a_failing_queue_backs_off_instead_of_spinning(self):
        """A poll that raises must sleep before retrying. The old loop reached
        its end-of-pass sleep after an error (``had_messages`` stayed False);
        without an explicit sleep the per-queue loop would busy-spin on a
        persistently failing queue and burn a core."""
        consumer = _consumer(poll_interval_s=0.05)
        calls = []

        def _always_fails(_queue):
            calls.append(time.monotonic())
            raise RuntimeError("broker down")

        consumer.consume_from_queue = MagicMock(side_effect=_always_fails)
        consumer._callbacks[RESULTS] = [lambda d: None]

        _run_threads(consumer, [RESULTS], 0.2)

        assert 0 < len(calls) <= 10, (
            f"expected ~4 attempts at a 50ms back-off, got {len(calls)} — "
            "the loop is spinning on the failure"
        )


class TestCooldownScopeSurvives:
    """SA-1470's semantics have to hold across the new thread layout: the
    default 'instance' scope is shared state precisely because the broker's
    REST limit is per service instance."""

    def test_instance_scope_pauses_every_queue(self):
        consumer = _consumer(cooldown_scope="instance")
        consumer.consume_from_queue = MagicMock(return_value=None)
        consumer._callbacks[RESULTS] = [lambda d: None]
        consumer._callbacks[ACTIONS] = [lambda d: None]
        consumer._cooldowns[""] = (time.monotonic() + 5, 1)

        _run_threads(consumer, [RESULTS, ACTIONS], 0.12)

        consumer.consume_from_queue.assert_not_called()

    def test_queue_scope_pauses_only_the_throttled_queue(self):
        consumer = _consumer(cooldown_scope="queue")
        polled = []
        consumer.consume_from_queue = MagicMock(
            side_effect=lambda q: polled.append(q) or None,
        )
        consumer._callbacks[RESULTS] = [lambda d: None]
        consumer._callbacks[ACTIONS] = [lambda d: None]
        consumer._cooldowns[RESULTS] = (time.monotonic() + 5, 1)

        _run_threads(consumer, [RESULTS, ACTIONS], 0.12)

        assert RESULTS not in polled
        assert ACTIONS in polled


class TestTransportObservability:
    def test_rest_gauge_is_emitted_once_not_once_per_queue(self):
        """The gauge answers 'which transport is in use', and that does not
        change as queues are added. Re-emitting per queue would be harmless for
        a gauge but misleading in the counter-shaped metrics around it."""
        metrics = MagicMock()
        consumer = _consumer(metrics=metrics)
        consumer.consume_from_queue = MagicMock(return_value=None)
        consumer._callbacks[RESULTS] = [lambda d: None]
        consumer._callbacks[ACTIONS] = [lambda d: None]

        consumer._start_poll_loop(RESULTS)
        consumer._start_poll_loop(ACTIONS)
        try:
            metrics.record_consume_transport.assert_called_once_with("rest")
        finally:
            consumer.close()

    def test_transport_state_reports_rest_while_threads_run(self):
        consumer = _consumer()
        consumer.consume_from_queue = MagicMock(return_value=None)
        consumer._callbacks[RESULTS] = [lambda d: None]
        consumer._start_poll_loop(RESULTS)
        try:
            assert consumer.transport_state()["active"] == "rest"
        finally:
            consumer.close()
