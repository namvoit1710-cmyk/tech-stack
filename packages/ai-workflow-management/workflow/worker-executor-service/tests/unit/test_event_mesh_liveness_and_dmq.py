"""Nothing could see the executor failing to receive work.

Task intake had no instrumentation at all. Whether the executor was consuming
was visible only as the *absence* of task logs — which is exactly how the
control plane's three AMQP outages stayed invisible: links OPEN, zero
deliveries, no errors, no detaches, and runs hanging with every node
NOT_STARTED.

Two blind spots, and they fail differently:

**An attached link that delivers nothing.** `_receive_loop` only reconnects
when `run()` returns, i.e. on disconnect. A silent link never returns, so the
loop spins forever consuming nothing. The watchdog is the only thing that can
end that, and a stall needs BOTH halves — silence AND a queue with messages
waiting. Depth alone is not a stall here in particular: this service
deliberately holds work back when saturated, so a deep queue is often just
correct backpressure.

**Messages being dead-lettered.** Every transport signal reads healthy while
this happens, because the messages really were delivered and then thrown away.
A DMQ growing is the only signal that says work is being lost.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.layer4_frameworks.messaging import event_mesh_amqp_consumer as amod
from app.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)
from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig
from app.layer4_frameworks.messaging.event_mesh_metrics import EventMeshMetrics

NS = "simplemdg/event/core-2"
Q = f"{NS}/sub-workflow-tasks-amqp"
DMQ = f"{Q}-dmq"


class FakeBridge:
    def __init__(self, *, ready=True, deliveries=0, epoch=1):
        self.ready = ready
        self.deliveries = deliveries
        self.epoch = epoch
        self.stopped_for = None

    def request_stop(self, reason):
        self.stopped_for = reason


def _consumer(depth, bridge=None, metrics=None, concurrency=1):
    c = amod.EventMeshAMQPConsumer(
        host="em.example.com",
        get_token=lambda: ("tok", 0.0),
        message_handler=lambda _q, _m: True,
        queue_depth=(depth if callable(depth) else (lambda _q: depth)),
        metrics=metrics,
        concurrency=concurrency,
    )
    if bridge is not None:
        c._bridges[(Q, 0)] = bridge
    return c


# ---------------------------------------------------------------------------
# The watchdog
# ---------------------------------------------------------------------------


class TestAStallNeedsBothHalves:
    def test_silence_with_messages_waiting_stands_the_link_down(self):
        b = FakeBridge(deliveries=0)
        c = _consumer(depth=7, bridge=b)

        c.check_liveness(now=1000.0)                       # arms the window
        stood = c.check_liveness(now=1000.0 + amod._STALL_AFTER_S + 1)

        assert stood == [Q]
        assert b.stopped_for == "watchdog_stall"

    def test_silence_on_an_empty_queue_is_an_idle_system_not_a_stall(self):
        b = FakeBridge(deliveries=0)
        c = _consumer(depth=0, bridge=b)

        c.check_liveness(now=1000.0)
        assert c.check_liveness(now=9999.0) == []
        assert b.stopped_for is None

    def test_a_deep_queue_that_is_still_delivering_is_not_a_stall(self):
        """Here that is the normal shape: the executor holds work back when
        saturated, so depth builds while the link is perfectly healthy."""
        b = FakeBridge(deliveries=0)
        c = _consumer(depth=500, bridge=b)

        c.check_liveness(now=1000.0)
        b.deliveries = 3
        assert c.check_liveness(now=1000.0 + amod._STALL_AFTER_S + 1) == []
        assert b.stopped_for is None

    def test_an_unreadable_depth_is_not_a_stall(self):
        """The watchdog must never be the thing that kills consumption."""
        b = FakeBridge(deliveries=0)
        c = _consumer(depth=None, bridge=b)

        c.check_liveness(now=1000.0)
        assert c.check_liveness(now=9999.0) == []

    def test_a_depth_lookup_that_raises_is_swallowed(self):
        def _boom(_q):
            raise RuntimeError("management down")

        b = FakeBridge(deliveries=0)
        c = _consumer(depth=_boom, bridge=b)

        assert c.check_liveness(now=1000.0) == []

    def test_a_link_that_is_not_attached_is_the_reconnect_loops_problem(self):
        b = FakeBridge(ready=False)
        c = _consumer(depth=7, bridge=b)

        c.check_liveness(now=1000.0)
        assert c.check_liveness(now=9999.0) == []
        assert b.stopped_for is None

    def test_the_window_widens_after_repeated_forced_reconnects(self):
        """A queue that genuinely cannot deliver must not reconnect-storm."""
        b = FakeBridge(deliveries=0)
        c = _consumer(depth=7, bridge=b)

        c.check_liveness(now=0.0)
        c.check_liveness(now=amod._STALL_AFTER_S + 1)       # 1st stand-down
        assert c.forced_reconnects == 1

        c.check_liveness(now=amod._STALL_AFTER_S + 2)       # re-arms
        # One plain window is no longer enough.
        c.check_liveness(now=amod._STALL_AFTER_S * 2 + 3)
        assert c.forced_reconnects == 1
        c.check_liveness(now=amod._STALL_AFTER_S * 3 + 4)
        assert c.forced_reconnects == 2

    def test_without_a_depth_source_the_watchdog_stays_out_of_the_way(self):
        c = amod.EventMeshAMQPConsumer(
            host="em.example.com",
            get_token=lambda: ("tok", 0.0),
            message_handler=lambda _q, _m: True,
        )
        c._bridges[Q] = FakeBridge(deliveries=0)

        assert c.check_liveness(now=9999.0) == []


class TestTheWatchdogReportsWhatItSaw:
    def test_progress_is_reported_as_deliveries_not_as_a_flat_gauge(self):
        m = MagicMock()
        b = FakeBridge(deliveries=0)
        c = _consumer(depth=0, bridge=b, metrics=m)

        c.check_liveness(now=1000.0)
        b.deliveries = 12
        c.check_liveness(now=1001.0)

        m.record_amqp_delivery.assert_called_once_with(Q, 12)

    def test_only_the_delta_is_reported(self):
        m = MagicMock()
        b = FakeBridge(deliveries=0)
        c = _consumer(depth=0, bridge=b, metrics=m)

        c.check_liveness(now=1000.0)
        b.deliveries = 12
        c.check_liveness(now=1001.0)
        b.deliveries = 20
        c.check_liveness(now=1002.0)

        assert [call.args for call in m.record_amqp_delivery.call_args_list] == [
            (Q, 12), (Q, 8),
        ]

    def test_a_stand_down_is_reported(self):
        m = MagicMock()
        c = _consumer(depth=7, bridge=FakeBridge(deliveries=0), metrics=m)

        c.check_liveness(now=1000.0)
        c.check_liveness(now=1000.0 + amod._STALL_AFTER_S + 1)

        m.record_amqp_stall.assert_called_once_with(Q)

    def test_a_metrics_failure_never_breaks_the_check(self):
        m = MagicMock()
        m.record_amqp_links.side_effect = RuntimeError("scrape blew up")
        c = _consumer(depth=0, bridge=FakeBridge(deliveries=1), metrics=m)

        c.check_liveness(now=1000.0)  # does not raise


# ---------------------------------------------------------------------------
# Token expiry
# ---------------------------------------------------------------------------


class TestAReconnectDoesNotLookLikeSilence:
    """`deliveries` restarts at 0 on every new bridge, but the watchdog's
    baseline used to live per QUEUE and survive the swap. So after any
    reconnect it compared a counter starting at 0 against the previous link's
    lifetime total, read "no progress", and — if the queue had work waiting —
    stood down a link that was delivering perfectly well. Its replacement then
    started from 0 again.

    That is a self-perpetuating false stall which also flatlines the delivery
    counter: the exact symptom set the watchdog exists to detect, manufactured
    by the watchdog. It needed no concurrency to fire — a 12-hour token
    rotation was enough.
    """

    def test_a_fresh_bridge_gets_a_fresh_baseline(self):
        c = _consumer(depth=0, bridge=FakeBridge(deliveries=5000, epoch=1))
        c.check_liveness(now=1000.0)                       # records 5000

        c._bridges[(Q, 0)] = FakeBridge(deliveries=2, epoch=2)   # reconnect
        m = MagicMock(); c._metrics = m
        c.check_liveness(now=1001.0)

        m.record_amqp_delivery.assert_called_once_with(Q, 2)

    def test_a_reconnect_on_a_busy_queue_is_not_stood_down(self):
        """The failure in full: old total 5000, new link delivering, queue deep."""
        c = _consumer(depth=500, bridge=FakeBridge(deliveries=5000, epoch=1))
        c.check_liveness(now=0.0)

        new = FakeBridge(deliveries=3, epoch=2)
        c._bridges[(Q, 0)] = new
        for t in (1.0, amod._STALL_AFTER_S + 2, amod._STALL_AFTER_S * 4 + 3):
            c.check_liveness(now=t)
            new.deliveries += 3

        assert new.stopped_for is None
        assert c.forced_reconnects == 0

    def test_a_genuinely_silent_new_bridge_is_still_stood_down(self):
        """The reset must not blind the watchdog — a fresh link that delivers
        NOTHING against a non-empty queue is still a stall."""
        c = _consumer(depth=7, bridge=FakeBridge(deliveries=5000, epoch=1))
        c.check_liveness(now=0.0)

        silent = FakeBridge(deliveries=0, epoch=2)
        c._bridges[(Q, 0)] = silent
        c.check_liveness(now=1.0)                              # arms
        c.check_liveness(now=amod._STALL_AFTER_S + 2)

        assert silent.stopped_for == "watchdog_stall"

    def test_the_forced_streak_survives_the_reset(self):
        """Otherwise every stand-down creates a new bridge, the streak resets,
        and the window never widens — a dead queue reconnect-storms forever."""
        c = _consumer(depth=7, bridge=FakeBridge(deliveries=0, epoch=1))
        c.check_liveness(now=0.0)
        c.check_liveness(now=amod._STALL_AFTER_S + 1)          # 1st stand-down
        assert c.forced_reconnects == 1

        c._bridges[(Q, 0)] = FakeBridge(deliveries=0, epoch=2)  # its replacement
        c.check_liveness(now=amod._STALL_AFTER_S + 2)           # arms
        c.check_liveness(now=amod._STALL_AFTER_S * 2 + 3)       # one window: too soon
        assert c.forced_reconnects == 1
        c.check_liveness(now=amod._STALL_AFTER_S * 3 + 4)       # widened window
        assert c.forced_reconnects == 2

    def test_epochs_are_unique_per_bridge(self):
        c = _consumer(depth=0)
        assert len({c._next_epoch() for _ in range(50)}) == 50


class TestEveryLinkIsWatched:
    """`concurrency` opens that many links onto the SAME queue. Keyed by queue,
    they overwrote each other in `_bridges` and only the last to register was
    ever watched — the other N-1 could go silent unseen, and `links_attached`
    reported 1 while 4 existed."""

    def _four_links(self, depth):
        c = _consumer(depth=depth, concurrency=4)
        bridges = [FakeBridge(deliveries=0, epoch=i + 1) for i in range(4)]
        for i, b in enumerate(bridges):
            c._bridges[(Q, i)] = b
        return c, bridges

    def test_all_four_are_registered_not_collapsed_to_one(self):
        c, bridges = self._four_links(depth=0)
        assert len(c._bridges) == 4

    def test_links_attached_counts_links_not_queues(self):
        m = MagicMock()
        c, _ = self._four_links(depth=0)
        c._metrics = m

        c.check_liveness(now=1000.0)

        m.record_amqp_links.assert_called_once_with(4, 0)

    def test_a_silent_link_among_healthy_ones_is_caught(self):
        c, bridges = self._four_links(depth=7)
        c.check_liveness(now=0.0)
        for b in bridges[:3]:
            b.deliveries += 10                     # three links are fine
        c.check_liveness(now=amod._STALL_AFTER_S + 1)

        assert [b.stopped_for for b in bridges] == [None, None, None, "watchdog_stall"]

    def test_the_queue_depth_is_read_once_per_pass_not_once_per_link(self):
        """Otherwise raising concurrency multiplies management-API calls."""
        calls = []
        c, _ = self._four_links(depth=lambda q: (calls.append(q), 7)[1])
        c.check_liveness(now=0.0)

        assert calls == [Q]


class TestTokenExpiry:
    def test_a_link_stands_down_before_its_credential_goes_stale(self):
        """The token is good for 12 hours, so a healthy link crosses expiry
        about once a day. `_receive_loop` already takes a fresh token on every
        reconnect — what was missing was a reason to reconnect."""
        import time

        b = amod._ProtonWsBridge(
            url="wss://x/y", address="queue:q", queue_name=Q, token="tok",
            prefetch=1, message_handler=lambda _q, _m: True, is_running=lambda: True,
            expires_at=time.time() + 10,  # inside the margin
        )
        assert b._token_expiring() is True

    def test_a_fresh_token_does_not_trigger_a_reconnect(self):
        import time

        b = amod._ProtonWsBridge(
            url="wss://x/y", address="queue:q", queue_name=Q, token="tok",
            prefetch=1, message_handler=lambda _q, _m: True, is_running=lambda: True,
            expires_at=time.time() + amod._TOKEN_RECONNECT_MARGIN_S + 3600,
        )
        assert b._token_expiring() is False

    def test_an_unknown_expiry_never_forces_a_reconnect(self):
        b = amod._ProtonWsBridge(
            url="wss://x/y", address="queue:q", queue_name=Q, token="tok",
            prefetch=1, message_handler=lambda _q, _m: True, is_running=lambda: True,
        )
        assert b._token_expiring() is False

    def test_a_stand_down_is_not_an_error(self):
        """The reconnect loop must not back off after a deliberate rotation."""
        b = amod._ProtonWsBridge(
            url="wss://x/y", address="queue:q", queue_name=Q, token="tok",
            prefetch=1, message_handler=lambda _q, _m: True, is_running=lambda: True,
        )
        b.request_stop("token_expiry")

        assert b.stood_down == "token_expiry"
        assert b.error is None
        assert b._closed is True


# ---------------------------------------------------------------------------
# The DMQ watch
# ---------------------------------------------------------------------------


def _client(**overrides) -> EventMeshBrokerClient:
    defaults = dict(
        token_url="https://auth.example.com/oauth/token",
        messaging_url="https://messaging.example.com",
        management_url="https://management.example.com",
        client_id="id", client_secret="secret", namespace=NS,
    )
    defaults.update(overrides)
    c = EventMeshBrokerClient(EventMeshConfig(**defaults))
    c._callbacks[Q] = [lambda _d: None]
    c._metrics = MagicMock()
    return c


def _with_depths(*depths) -> EventMeshBrokerClient:
    c = _client()
    c.queue_depth = MagicMock(side_effect=list(depths))
    return c


class TestItReportsLoss:
    def test_growth_is_reported_as_newly_dead_lettered(self):
        c = _with_depths(0, 3)

        c.sample_dead_letters()   # baseline
        c.sample_dead_letters()   # +3

        c._metrics.record_dead_lettered.assert_called_once_with(DMQ, 3)

    def test_only_the_delta_is_counted_not_the_running_total(self):
        c = _with_depths(0, 3, 5)
        for _ in range(3):
            c.sample_dead_letters()

        assert [call.args for call in c._metrics.record_dead_lettered.call_args_list] \
            == [(DMQ, 3), (DMQ, 2)]

    def test_the_total_depth_is_gauged_every_pass(self):
        c = _with_depths(4, 4)
        c.sample_dead_letters()
        c.sample_dead_letters()

        assert [call.args for call in c._metrics.record_dmq_depth.call_args_list] \
            == [(4,), (4,)]


class TestItDoesNotCryWolf:
    def test_a_pre_existing_backlog_is_not_reported_as_fresh_loss(self):
        """Otherwise every deploy alarms on tasks lost weeks ago — and an alert
        that fires on every restart gets muted."""
        c = _with_depths(7)

        c.sample_dead_letters()

        c._metrics.record_dead_lettered.assert_not_called()
        c._metrics.record_dmq_depth.assert_called_once_with(7)

    def test_a_steady_depth_is_not_loss(self):
        c = _with_depths(2, 2, 2)
        for _ in range(3):
            c.sample_dead_letters()

        c._metrics.record_dead_lettered.assert_not_called()

    def test_a_drained_dmq_never_decrements_the_counter(self):
        c = _with_depths(9, 0)
        c.sample_dead_letters()
        c.sample_dead_letters()

        c._metrics.record_dead_lettered.assert_not_called()

    def test_a_drain_then_regrowth_is_measured_from_the_new_floor(self):
        c = _with_depths(9, 0, 2)
        for _ in range(3):
            c.sample_dead_letters()

        c._metrics.record_dead_lettered.assert_called_once_with(DMQ, 2)


class TestItNeverBreaksConsumption:
    def test_an_unreadable_dmq_is_skipped_not_raised(self):
        c = _client()
        c.queue_depth = MagicMock(side_effect=RuntimeError("management down"))

        assert c.sample_dead_letters() == {}
        c._metrics.record_dead_lettered.assert_not_called()

    def test_an_unknown_depth_is_not_treated_as_zero(self):
        """None means 'could not tell'. Calling that 0 reports a phantom drain,
        and then phantom loss on the next real reading."""
        c = _with_depths(5, None, 5)
        for _ in range(3):
            c.sample_dead_letters()

        c._metrics.record_dead_lettered.assert_not_called()

    def test_a_dmq_is_not_given_a_dmq_of_its_own(self):
        c = _client()
        c._callbacks.clear()
        c._callbacks[DMQ] = [lambda _d: None]
        c.queue_depth = MagicMock(return_value=0)

        assert c.sample_dead_letters() == {}


class TestLifecycle:
    def test_the_watch_starts_once(self):
        c = _client()
        c._dmq_check_seconds = 60
        c._start_dmq_watch()
        first = c._dmq_thread
        c._start_dmq_watch()

        assert c._dmq_thread is first
        c.close()

    def test_close_stops_it(self):
        c = _client()
        c._dmq_check_seconds = 60
        c._start_dmq_watch()
        c.close()

        assert c._dmq_thread is None
        assert c._dmq_stop.is_set()

    def test_the_first_sample_waits_for_every_subscription_to_land(self):
        """The watch starts inside the FIRST subscribe_to_topic. Sampling
        immediately would count only the queues registered so far and publish a
        depth of 0 that means 'not counted yet', not 'nothing dead-lettered'."""
        c = _client()
        c._dmq_check_seconds = 30
        c.sample_dead_letters = MagicMock()
        c._dmq_stop.set()  # loop exits on the very first wait

        c._dmq_watch_loop()

        c.sample_dead_letters.assert_not_called()


# ---------------------------------------------------------------------------
# The metric shape
# ---------------------------------------------------------------------------


class TestDeliveryCountIsAnAmountNotALabel:
    def test_counts_add_up_into_one_series(self):
        """Shipped as a LABEL on the control plane first: the counter then
        reads 1 and every distinct delta forks a new series (measured: six
        queues, 19 series in five minutes), which turns rate() — the positive
        health signal this metric exists for — into noise."""
        from prometheus_client import CollectorRegistry
        from app.layer4_frameworks.monitoring import prometheus as pmod

        registry = CollectorRegistry()
        counter = pmod.Counter(
            "t_delivery_total", "test", ["queue"], registry=registry,
        )
        counter.labels(queue=Q).inc(47)
        counter.labels(queue=Q).inc(3)

        series = [
            s for metric in registry.collect() for s in metric.samples
            if s.name == "t_delivery_total"
        ]
        assert len(series) == 1
        assert series[0].value == 50.0
        assert "count" not in series[0].labels

    def test_a_zero_or_negative_delta_never_touches_the_counter(self):
        """A Prometheus counter cannot go backwards — `.inc(-1)` raises, and an
        exception here would come out of the watchdog thread."""
        from app.layer4_frameworks.monitoring.prometheus import PrometheusMonitor

        monitor = PrometheusMonitor()
        monitor.eventmesh_amqp_delivery(Q, 0)     # does not raise
        monitor.eventmesh_amqp_delivery(Q, -5)    # does not raise
        monitor.eventmesh_dead_lettered(DMQ, 0)   # does not raise


class TestMetricsAreNeverTheFailure:
    def test_a_missing_monitor_is_a_no_op(self):
        EventMeshMetrics(None).record_amqp_stall(Q)  # does not raise

    def test_a_monitor_without_the_method_is_a_no_op(self):
        EventMeshMetrics(object()).record_dmq_depth(3)  # does not raise

    def test_a_monitor_that_raises_is_swallowed(self):
        m = MagicMock()
        m.eventmesh_dmq_depth.side_effect = RuntimeError("scrape blew up")

        EventMeshMetrics(m).record_dmq_depth(3)  # does not raise

    def test_the_real_monitor_implements_every_method_the_wrapper_calls(self):
        """A wrapper that silently no-ops on a typo is worse than no metric —
        it reads as covered."""
        from app.layer4_frameworks.monitoring.prometheus import PrometheusMonitor

        monitor = PrometheusMonitor()
        for name in (
            "eventmesh_amqp_delivery", "eventmesh_amqp_stall",
            "eventmesh_amqp_reconnect", "eventmesh_amqp_links",
            "eventmesh_consume_transport", "eventmesh_consume_fallback",
            "eventmesh_dead_lettered", "eventmesh_dmq_depth",
        ):
            assert callable(getattr(monitor, name, None)), f"missing {name}"
