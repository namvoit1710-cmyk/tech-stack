"""P4 — make the silent-link failure visible from outside the process.

The three tenant-1 AMQP outages were undetectable: six links OPEN, zero
deliveries, no errors. The watchdog (G2) now evaluates the condition; this
exports the verdict.

The alert is `eventmesh.amqp.stall`, emitted where all three facts (attached,
depth, delivery counter) are already in hand — not reassembled from three
separate series in a query language.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from app.layer4_frameworks.messaging import event_mesh_amqp_consumer as amod
from app.layer4_frameworks.messaging.event_mesh_metrics import EventMeshMetrics

QUEUE = "simplemdg/event/core-2/sub-orchestration-actions-amqp"


def _consumer(depth_fn, metrics):
    return amod.EventMeshAMQPConsumer(
        host="em.example.com",
        get_token=lambda: ("tok", time.time() + 43_200),
        message_handler=lambda _q, _b: True,
        queue_depth=depth_fn,
        metrics=metrics,
    )


def _bridge(*, ready=True, deliveries=0, epoch=1):
    b = MagicMock()
    b.ready = ready
    b.deliveries = deliveries
    b.epoch = epoch
    return b


class TestStallIsExported:
    def test_a_stall_emits_the_alert_metric(self):
        metrics = MagicMock()
        c = _consumer(lambda _q: 7, metrics)
        c._bridges[(QUEUE, 0)] = _bridge()
        t = 1000.0
        c.check_liveness(now=t)
        c.check_liveness(now=t + amod._STALL_AFTER_S + 1)
        metrics.record_amqp_stall.assert_called_once_with(QUEUE)

    def test_a_healthy_link_never_emits_a_stall(self):
        metrics = MagicMock()
        c = _consumer(lambda _q: 7, metrics)
        b = _bridge()
        c._bridges[(QUEUE, 0)] = b
        for i in range(6):
            b.deliveries += 2
            c.check_liveness(now=1000.0 + i * 60)
        metrics.record_amqp_stall.assert_not_called()

    def test_deliveries_are_reported_as_a_delta(self):
        metrics = MagicMock()
        c = _consumer(lambda _q: 0, metrics)
        b = _bridge(deliveries=0)
        c._bridges[(QUEUE, 0)] = b
        b.deliveries = 5
        c.check_liveness(now=1000.0)
        b.deliveries = 12
        c.check_liveness(now=1060.0)
        assert [call.args for call in metrics.record_amqp_delivery.call_args_list] == [
            (QUEUE, 5), (QUEUE, 7),
        ]

    def test_gauges_report_attached_and_stalled(self):
        metrics = MagicMock()
        c = _consumer(lambda _q: 7, metrics)
        c._bridges[(QUEUE, 0)] = _bridge()
        c._bridges[("other", 0)] = _bridge(ready=False)

        c.check_liveness(now=1000.0)
        # one attached, one stalling (clock armed), the not-ready one excluded
        assert metrics.record_amqp_links.call_args.args == (1, 1)

    def test_an_idle_system_reports_zero_stalled(self):
        metrics = MagicMock()
        c = _consumer(lambda _q: 0, metrics)
        c._bridges[(QUEUE, 0)] = _bridge()
        c.check_liveness(now=1000.0)
        assert metrics.record_amqp_links.call_args.args == (1, 0)

    def test_a_broken_metrics_sink_does_not_stop_consumption(self):
        """Reporting must never be the thing that breaks the transport."""
        metrics = MagicMock()
        metrics.record_amqp_links.side_effect = RuntimeError("monitor down")
        metrics.record_amqp_stall.side_effect = RuntimeError("monitor down")
        c = _consumer(lambda _q: 7, metrics)
        b = _bridge()
        c._bridges[(QUEUE, 0)] = b
        t = 1000.0
        c.check_liveness(now=t)
        assert c.check_liveness(now=t + amod._STALL_AFTER_S + 1) == [QUEUE]
        b.request_stop.assert_called_once()

    def test_no_metrics_sink_is_fine(self):
        c = _consumer(lambda _q: 7, None)
        c._bridges[(QUEUE, 0)] = _bridge()
        c.check_liveness(now=1000.0)  # must not raise


class TestReconnectReasons:
    def test_the_reasons_are_distinguishable(self):
        """A routine 12-hour token rotation and a watchdog stand-down must not
        look the same on a dashboard — one is normal, one is a fault."""
        monitor = MagicMock()
        metrics = EventMeshMetrics(monitor)
        metrics.record_amqp_reconnect(QUEUE, "token_expiry")
        metrics.record_amqp_reconnect(QUEUE, "watchdog_stall")
        reasons = [c.kwargs["tags"]["reason"] for c in monitor.increment.call_args_list]
        assert reasons == ["token_expiry", "watchdog_stall"]


class TestMetricsWrapper:
    def test_a_missing_monitor_is_a_no_op(self):
        m = EventMeshMetrics(None)
        m.record_amqp_stall(QUEUE)
        m.record_amqp_links(1, 0)
        m.record_amqp_delivery(QUEUE, 3)

    def test_a_throwing_monitor_is_swallowed(self):
        monitor = MagicMock()
        monitor.increment.side_effect = RuntimeError("down")
        monitor.track.side_effect = RuntimeError("down")
        m = EventMeshMetrics(monitor)
        m.record_amqp_stall(QUEUE)
        m.record_amqp_links(1, 0)

    def test_zero_deliveries_are_not_reported(self):
        monitor = MagicMock()
        EventMeshMetrics(monitor).record_amqp_delivery(QUEUE, 0)
        monitor.increment.assert_not_called()


class TestPrefetchDefault:
    """P3 — prefetch is the depth behind a SERIAL handler (concurrency 1), and
    the broker starts its redelivery clock when it hands a message over, not
    when the handler reaches it. Against the 60 s queue lock TTL, 50 tolerated
    only ~1.2 s per message; 10 tolerates ~6 s.
    """

    def test_the_default_is_ten(self):
        from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig
        cfg = EventMeshConfig(
            token_url="u", messaging_url="m", management_url="g",
            client_id="i", client_secret="s",
        )
        assert cfg.amqp_prefetch == 10

    def test_prefetch_stays_well_under_the_lock_ttl_budget(self):
        """The invariant, stated as arithmetic so a future bump has to face it:
        prefetch * assumed-per-message-seconds must stay under the lock TTL."""
        from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig
        cfg = EventMeshConfig(
            token_url="u", messaging_url="m", management_url="g",
            client_id="i", client_secret="s",
        )
        lock_ttl_s = cfg.lock_duration_ms / 1000
        assert cfg.amqp_prefetch * 5.0 <= lock_ttl_s, (
            "prefetch leaves less than 5s per message before the broker "
            "redelivers work that is still in progress"
        )

    def test_concurrency_stays_at_one_for_ordering(self):
        from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig
        cfg = EventMeshConfig(
            token_url="u", messaging_url="m", management_url="g",
            client_id="i", client_secret="s", amqp_concurrency=4,
        )
        # Explicitly configurable, but the default must not drift off 1.
        assert EventMeshConfig(
            token_url="u", messaging_url="m", management_url="g",
            client_id="i", client_secret="s",
        ).amqp_concurrency == 1
        assert cfg.amqp_concurrency == 4


class TestDeliveryCountIsAnAmountNotALabel:
    """`count` in a LABEL makes the counter read 1 and forks a new series.

    Measured on tenant-1 2026-08-05 (`solace-971`), scraped live:

        eventmesh_amqp_delivery_total{count="47",queue="…triggers-amqp"} 1.0

    Six queues produced 19 series in five minutes, the value never reflected
    the deliveries, and `rate(eventmesh.amqp.delivery)` — which the P4 runbook
    names as THE positive health signal — could not work at all.
    """

    def test_the_count_is_passed_as_the_amount(self):
        monitor = MagicMock()
        EventMeshMetrics(monitor).record_amqp_delivery(QUEUE, 47)

        monitor.increment.assert_called_once_with(
            "eventmesh.amqp.delivery", tags={"queue": QUEUE}, amount=47,
        )

    def test_the_count_never_appears_in_the_tags(self):
        monitor = MagicMock()
        EventMeshMetrics(monitor).record_amqp_delivery(QUEUE, 47)

        tags = monitor.increment.call_args.kwargs["tags"]
        assert list(tags.keys()) == ["queue"], (
            "any per-delivery value in the tags forks the series"
        )

    def test_prometheus_counts_deliveries_not_calls(self):
        from prometheus_client import CollectorRegistry, generate_latest

        from app.layer4_frameworks.monitoring.prometheus import PrometheusMonitor

        registry = CollectorRegistry()
        metrics = EventMeshMetrics(PrometheusMonitor(registry))
        metrics.record_amqp_delivery(QUEUE, 47)
        metrics.record_amqp_delivery(QUEUE, 3)

        body = generate_latest(registry).decode()
        lines = [ln for ln in body.splitlines()
                 if ln.startswith("eventmesh_amqp_delivery_total")]

        assert len(lines) == 1, f"one queue must be one series, got: {lines}"
        assert lines[0].endswith(" 50.0"), f"expected 47+3=50, got: {lines[0]}"

    def test_a_monitor_without_amount_still_records_every_delivery(self):
        """IMonitor is a Protocol. A monitor predating `amount` must not make
        the metrics vanish — the blanket `except` would have swallowed it."""
        calls = []

        class LegacyMonitor:
            def increment(self, metric, tags=None):
                calls.append((metric, dict(tags or {})))

        EventMeshMetrics(LegacyMonitor()).record_amqp_delivery(QUEUE, 3)

        assert len(calls) == 3
        assert all(c == ("eventmesh.amqp.delivery", {"queue": QUEUE}) for c in calls)

    def test_a_non_positive_amount_never_moves_a_prometheus_counter(self):
        from prometheus_client import CollectorRegistry

        from app.layer4_frameworks.monitoring.prometheus import PrometheusMonitor

        monitor = PrometheusMonitor(CollectorRegistry())
        # Counters cannot go backwards; prometheus_client raises on inc(<0).
        monitor.increment("eventmesh.amqp.delivery", {"queue": QUEUE}, amount=0)
        monitor.increment("eventmesh.amqp.delivery", {"queue": QUEUE}, amount=-5)
