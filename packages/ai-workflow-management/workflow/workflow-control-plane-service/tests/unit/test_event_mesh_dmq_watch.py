"""Nothing was watching the dead-message queues.

Every Event Mesh signal describes the *transport* — links attached, stalls,
reconnects, deliveries — and on tenant-1 2026-08-05 all of them read perfectly
healthy while messages were being destroyed, because the messages really were
delivered and then thrown away. A DMQ filling up is the only signal that says
**work is being lost**, and it had no metric at all; the runbook's "check the
DMQ" in triage only helps someone already looking.

Found the hard way: `sub-workflow-triggers-amqp-dmq` was discovered holding a
real Jira webhook event — a trigger that never ran its workflow — only because
a queue-depth probe happened to print DMQ depths.

Lives in `EventMeshConsumer`, not the AMQP watchdog, because that watchdog does
not run under REST — and REST is the documented rollback target, i.e. exactly
when you most need to know whether messages are being dead-lettered.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)
from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig

NAMESPACE = "simplemdg/event/core-2"
Q = f"{NAMESPACE}/sub-workflow-triggers-amqp"
DMQ = f"{Q}-dmq"


def _consumer(depths):
    """A consumer subscribed to one queue, whose DMQ reports `depths` in turn."""
    client = EventMeshBrokerClient(EventMeshConfig(
        token_url="https://auth.example.com/oauth/token",
        messaging_url="https://messaging.example.com",
        management_url="https://management.example.com",
        client_id="id", client_secret="secret", namespace=NAMESPACE,
        consume_transport="amqp",
    ))
    consumer = client._consumer
    consumer._callbacks[Q] = [lambda _d: None]
    consumer._admin.queue_depth = MagicMock(side_effect=list(depths))
    consumer._metrics = MagicMock()
    return consumer


class TestItReportsLoss:
    def test_growth_is_reported_as_newly_dead_lettered(self):
        c = _consumer([0, 3])

        c.sample_dead_letters()          # baseline
        c.sample_dead_letters()          # +3

        c._metrics.record_dead_lettered.assert_called_once_with(DMQ, 3)

    def test_only_the_delta_is_counted_not_the_running_total(self):
        c = _consumer([0, 3, 5])
        c.sample_dead_letters()
        c.sample_dead_letters()
        c.sample_dead_letters()

        assert [call.args for call in c._metrics.record_dead_lettered.call_args_list] \
            == [(DMQ, 3), (DMQ, 2)]

    def test_the_total_depth_is_gauged_every_pass(self):
        c = _consumer([4, 4])
        c.sample_dead_letters()
        c.sample_dead_letters()

        assert [call.args for call in c._metrics.record_dmq_depth.call_args_list] \
            == [(4,), (4,)]


class TestItDoesNotCryWolf:
    def test_a_pre_existing_backlog_is_not_reported_as_fresh_loss(self):
        """Otherwise every deploy would alarm on messages lost weeks ago —
        and an alert that fires on every restart gets muted."""
        c = _consumer([7])

        c.sample_dead_letters()

        c._metrics.record_dead_lettered.assert_not_called()
        c._metrics.record_dmq_depth.assert_called_once_with(7)

    def test_a_steady_depth_is_not_loss(self):
        c = _consumer([2, 2, 2])
        for _ in range(3):
            c.sample_dead_letters()

        c._metrics.record_dead_lettered.assert_not_called()

    def test_a_drained_dmq_never_decrements_the_counter(self):
        """Someone emptying the DMQ is not negative loss."""
        c = _consumer([9, 0])
        c.sample_dead_letters()
        c.sample_dead_letters()

        c._metrics.record_dead_lettered.assert_not_called()

    def test_a_drain_then_regrowth_is_measured_from_the_new_floor(self):
        c = _consumer([9, 0, 2])
        c.sample_dead_letters()
        c.sample_dead_letters()
        c.sample_dead_letters()

        c._metrics.record_dead_lettered.assert_called_once_with(DMQ, 2)


class TestItNeverBreaksConsumption:
    def test_an_unreadable_dmq_is_skipped_not_raised(self):
        c = _consumer([])
        c._admin.queue_depth = MagicMock(side_effect=RuntimeError("management down"))

        assert c.sample_dead_letters() == {}
        c._metrics.record_dead_lettered.assert_not_called()

    def test_an_unknown_depth_is_not_treated_as_zero(self):
        """None means 'could not tell', and calling that 0 would report a
        phantom drain — and then phantom loss on the next real reading."""
        c = _consumer([5, None, 5])
        c.sample_dead_letters()
        c.sample_dead_letters()
        c.sample_dead_letters()

        c._metrics.record_dead_lettered.assert_not_called()

    def test_a_dmq_is_not_given_a_dmq_of_its_own(self):
        c = _consumer([0])
        c._callbacks.clear()
        c._callbacks[DMQ] = [lambda _d: None]

        assert c.sample_dead_letters() == {}


class TestLifecycle:
    def test_the_watch_starts_once(self):
        c = _consumer([0, 0, 0, 0])
        c._start_dmq_watch()
        first = c._dmq_thread
        c._start_dmq_watch()

        assert c._dmq_thread is first
        c.close()

    def test_close_stops_it(self):
        c = _consumer([0, 0, 0, 0])
        c._start_dmq_watch()
        c.close()

        assert c._dmq_thread is None
        assert c._dmq_stop.is_set()
