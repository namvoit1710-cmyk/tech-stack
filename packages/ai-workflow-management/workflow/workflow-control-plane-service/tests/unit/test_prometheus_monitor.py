"""Tests for the real Prometheus-backed IMonitor."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, generate_latest

from app.layer4_frameworks.monitoring.prometheus import PrometheusMonitor


def _scrape(monitor: PrometheusMonitor) -> str:
    return generate_latest(monitor.registry).decode()


class TestLazyCreation:
    def test_counter_created_on_first_increment(self):
        m = PrometheusMonitor(CollectorRegistry())
        m.increment("req_total", tags={"status": "ok"})
        body = _scrape(m)
        # Prometheus strips a trailing '_total' from counter base names,
        # then re-adds it on the exposed timeseries. 'req_total' base →
        # 'req_total' on the wire (not 'req_total_total').
        assert 'req_total{status="ok"} 1.0' in body

    def test_histogram_created_on_first_observation(self):
        m = PrometheusMonitor(CollectorRegistry())
        m.histogram("latency_seconds", 0.042, tags={"route": "/x"})
        body = _scrape(m)
        assert "latency_seconds_bucket" in body
        assert "latency_seconds_sum" in body
        assert 'route="/x"' in body

    def test_gauge_created_on_first_track(self):
        m = PrometheusMonitor(CollectorRegistry())
        m.track("queue_depth", 42)
        body = _scrape(m)
        assert "queue_depth 42.0" in body

    def test_untagged_counter(self):
        m = PrometheusMonitor(CollectorRegistry())
        m.increment("hits_total")
        body = _scrape(m)
        assert "hits_total 1.0" in body


class TestCounterBehavior:
    def test_multiple_increments_accumulate(self):
        m = PrometheusMonitor(CollectorRegistry())
        for _ in range(3):
            m.increment("n", tags={"t": "a"})
        body = _scrape(m)
        assert 'n_total{t="a"} 3.0' in body

    def test_different_label_values_separate_series(self):
        m = PrometheusMonitor(CollectorRegistry())
        m.increment("n", tags={"t": "a"})
        m.increment("n", tags={"t": "b"})
        m.increment("n", tags={"t": "b"})
        body = _scrape(m)
        assert 'n_total{t="a"} 1.0' in body
        assert 'n_total{t="b"} 2.0' in body


class TestHistogramBehavior:
    def test_histogram_buckets_populated(self):
        m = PrometheusMonitor(CollectorRegistry())
        m.histogram("d", 0.002)
        m.histogram("d", 0.03)
        m.histogram("d", 1.0)
        body = _scrape(m)
        # The 0.005 bucket captures 0.002; the +Inf bucket captures all 3.
        assert 'd_bucket{le="0.005"} 1.0' in body
        assert 'd_bucket{le="+Inf"} 3.0' in body
        assert "d_count 3.0" in body


class TestLabelSetMismatchGuard:
    def test_second_call_with_different_labels_dropped(self, caplog):
        m = PrometheusMonitor(CollectorRegistry())
        m.increment("n", tags={"a": "1"})
        # Second call uses a different label set — should be dropped.
        m.increment("n", tags={"b": "2"})
        body = _scrape(m)
        # Original label set counted once.
        assert 'n_total{a="1"} 1.0' in body
        # The mismatched sample didn't sneak through under "b".
        assert 'b="2"' not in body

    def test_tagged_then_untagged_dropped(self):
        m = PrometheusMonitor(CollectorRegistry())
        m.increment("n", tags={"a": "1"})
        m.increment("n")  # no tags — label set mismatch
        body = _scrape(m)
        assert 'n_total{a="1"} 1.0' in body


class TestSharedRegistry:
    def test_same_registry_across_calls(self):
        r = CollectorRegistry()
        m1 = PrometheusMonitor(r)
        m2 = PrometheusMonitor(r)
        m1.increment("shared_total", tags={"x": "1"})
        # m2 uses the same registry; its metric name collides.
        # Creation fails with ValueError → drops silently.
        m2.increment("shared_total", tags={"x": "1"})
        body = generate_latest(r).decode()
        # Only m1's counter survived; m2's samples dropped.
        assert 'shared_total{x="1"} 1.0' in body
