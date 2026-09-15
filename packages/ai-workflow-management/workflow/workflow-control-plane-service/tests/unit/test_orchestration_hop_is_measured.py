"""How long does an orchestration action take to come back to us?

The control plane publishes ``ContinueRunAction`` / ``ExecuteNodeAction`` to
Event Mesh and consumes them in the SAME process. That hop was measured twice
in one run on tenant-1 — 0.065 s and 0.185 s — and the explanation offered for
the 3x, queueing in the send buffer, was refuted: the mean flush batch is 3.7
messages, not the 19 the model assumed. With no instrument on the delivery side
the gap was unattributable, so this adds one.

One wall clock covers both ends because producer and consumer are the same
process, so the difference is the whole hop: send-buffer wait, HTTPS POST,
broker fan-out, AMQP delivery, consumer thread pickup.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from app.layer2_application.interfaces.orchestration_queue_interface import (
    ContinueRunAction,
    ExecuteNodeAction,
)
from app.layer4_frameworks.orchestration_queue.kafka_queue import (
    BrokerOrchestrationQueue,
    _deserialize,
)


def _queue(monitor=None) -> BrokerOrchestrationQueue:
    q = BrokerOrchestrationQueue.__new__(BrokerOrchestrationQueue)
    q._monitor = monitor
    return q


class TestTheStamp:
    def test_publishing_stamps_the_action(self):
        broker = MagicMock()
        q = BrokerOrchestrationQueue(broker, monitor=MagicMock())
        q._topic_ensured = True
        before = time.time()
        q.enqueue_nowait(ExecuteNodeAction(run_id="r1", task_id="t1"))
        payload = broker.publish_to_topic.call_args.args[1]
        assert before <= payload["published_at"] <= time.time()


class TestTheMeasurement:
    def test_the_hop_is_recorded_against_the_action_type(self):
        monitor = MagicMock()
        action = ContinueRunAction(run_id="r1", published_at=time.time() - 0.2)
        _queue(monitor)._observe_hop(action)
        name, value = monitor.histogram.call_args.args[:2]
        assert name == "orchestration.hop_seconds"
        assert 0.15 < value < 0.5
        assert monitor.histogram.call_args.kwargs["tags"]["action"] == action.action_type

    def test_an_unstamped_action_is_not_measured(self):
        """In-memory queue, tests, and anything published before the field
        existed. Reporting 56 years of hop would be worse than reporting none."""
        monitor = MagicMock()
        _queue(monitor)._observe_hop(ContinueRunAction(run_id="r1"))
        monitor.histogram.assert_not_called()

    def test_a_clock_that_went_backwards_never_reports_negative(self):
        monitor = MagicMock()
        _queue(monitor)._observe_hop(
            ContinueRunAction(run_id="r1", published_at=time.time() + 5),
        )
        assert monitor.histogram.call_args.args[1] == 0.0

    def test_a_broken_monitor_cannot_drop_an_action(self):
        monitor = MagicMock()
        monitor.histogram.side_effect = RuntimeError("metrics are down")
        _queue(monitor)._observe_hop(
            ContinueRunAction(run_id="r1", published_at=time.time()),
        )

    def test_no_monitor_is_fine(self):
        _queue(None)._observe_hop(
            ContinueRunAction(run_id="r1", published_at=time.time()),
        )


class TestForwardCompatibility:
    """``_deserialize`` used to raise on an unknown key, get caught, and return
    None — which DROPS the action and hangs the run, silently. That made adding
    any field a rollback hazard: a pod on older code receiving a newer message
    would lose it. This is the bug that would have bitten this very change."""

    def test_an_unknown_field_is_ignored_not_fatal(self):
        action = _deserialize({
            "action_type": "continue", "run_id": "r1", "task_id": "t1",
            "test_mode": False, "published_at": 1.0,
            "a_field_from_the_future": "surprise",
        })
        assert action is not None
        assert action.run_id == "r1"

    def test_a_message_without_published_at_still_deserialises(self):
        action = _deserialize({
            "action_type": "execute_node", "run_id": "r1", "task_id": "t1",
        })
        assert action is not None and action.published_at == 0.0

    def test_an_unknown_action_type_is_still_refused(self):
        assert _deserialize({"action_type": "nope", "run_id": "r1"}) is None
