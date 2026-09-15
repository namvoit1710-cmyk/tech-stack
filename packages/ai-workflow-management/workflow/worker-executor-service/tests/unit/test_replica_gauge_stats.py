"""SA-2054 (R7) — pure age helper + gauge-snapshot aggregator (worker_liveness).

Uses lightweight row doubles (SimpleNamespace) with the attributes the functions
read: worker_type, delivery_mode, health_status, last_heartbeat, instance_id. No
store, no Prometheus.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from app.layer1_domain.value_objects.worker_liveness import (
    oldest_fresh_age_seconds,
    replica_gauge_stats,
)

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _row(worker_type="jira", delivery_mode="push", health_status="healthy",
         age_s=10, instance_id="i1"):
    """Row whose last_heartbeat is age_s seconds before _NOW (age_s=None -> blank)."""
    hb = "" if age_s is None else (_NOW - timedelta(seconds=age_s)).isoformat()
    return SimpleNamespace(worker_type=worker_type, delivery_mode=delivery_mode,
                           health_status=health_status, last_heartbeat=hb,
                           instance_id=instance_id)


class TestOldestFreshAge:
    def test_empty_rows_is_none(self):
        assert oldest_fresh_age_seconds([], 120, _NOW) is None

    def test_picks_oldest_among_fresh_healthy(self):
        rows = [_row(age_s=10), _row(age_s=90), _row(age_s=30)]
        assert oldest_fresh_age_seconds(rows, 120, _NOW) == 90

    def test_excludes_unhealthy(self):
        rows = [_row(age_s=10, health_status="unhealthy")]
        assert oldest_fresh_age_seconds(rows, 120, _NOW) is None

    def test_excludes_stale_beyond_window(self):
        rows = [_row(age_s=200)]  # older than stale_after=120
        assert oldest_fresh_age_seconds(rows, 120, _NOW) is None

    def test_excludes_blank_heartbeat(self):
        rows = [_row(age_s=None)]  # never heartbeated -> unmeasurable
        assert oldest_fresh_age_seconds(rows, 120, _NOW) is None

    def test_blank_status_counts_as_healthy(self):
        rows = [_row(age_s=15, health_status="")]  # HEALTHY_STATUSES includes ""
        assert oldest_fresh_age_seconds(rows, 120, _NOW) == 15


class TestReplicaGaugeStats:
    def test_counts_per_type_and_mode(self):
        rows = [
            _row(worker_type="jira", delivery_mode="push", instance_id="i1"),
            _row(worker_type="jira", delivery_mode="push", instance_id="i2"),
            _row(worker_type="jira", delivery_mode="pull", instance_id="i3"),
        ]
        snap = replica_gauge_stats(rows, 120, _NOW)
        assert snap["counts"][("jira", "push")] == {"total": 2, "healthy": 2}
        assert snap["counts"][("jira", "pull")] == {"total": 1, "healthy": 1}

    def test_healthy_uses_broad_set_unhealthy_excluded_from_healthy(self):
        rows = [
            _row(worker_type="slack", health_status="healthy", instance_id="i1"),
            _row(worker_type="slack", health_status="unhealthy", instance_id="i2"),
        ]
        snap = replica_gauge_stats(rows, 120, _NOW)
        assert snap["counts"][("slack", "push")] == {"total": 2, "healthy": 1}

    def test_age_per_type_none_when_no_fresh(self):
        rows = [_row(worker_type="dead", health_status="unhealthy", instance_id="i1")]
        snap = replica_gauge_stats(rows, 120, _NOW)
        assert snap["counts"][("dead", "push")] == {"total": 1, "healthy": 0}
        assert snap["age"]["dead"] is None

    def test_age_per_type_oldest_fresh(self):
        rows = [
            _row(worker_type="jira", age_s=10, instance_id="i1"),
            _row(worker_type="jira", age_s=45, instance_id="i2"),
        ]
        snap = replica_gauge_stats(rows, 120, _NOW)
        assert snap["age"]["jira"] == 45

    def test_enum_delivery_mode_value_used_as_label(self):
        from app.layer1_domain.value_objects.delivery_mode import DeliveryMode
        rows = [_row(worker_type="jira", delivery_mode=DeliveryMode.PULL, instance_id="i1")]
        snap = replica_gauge_stats(rows, 120, _NOW)
        assert ("jira", "pull") in snap["counts"]

    def test_blank_worker_type_skipped(self):
        rows = [_row(worker_type="", instance_id="i1")]
        snap = replica_gauge_stats(rows, 120, _NOW)
        assert snap["counts"] == {}
        assert snap["age"] == {}
