"""SA-2054 (R7) — PrometheusMonitor.refresh_replica_gauges against the module
_default_registry. get_sample_value returns None for an absent label set, which is
how the "age series removed" assertion works. Unique worker_type per test avoids
module-singleton bleed.
"""
from __future__ import annotations

from app.layer4_frameworks.monitoring.prometheus import (
    PrometheusMonitor,
    _default_registry,
)

TOTAL = "wes_worker_replicas_total"
HEALTHY = "wes_worker_replicas_healthy"
AGE = "wes_worker_replica_heartbeat_age_seconds"


def _snapshot(counts, age):
    return {"counts": counts, "age": age}


def test_sets_total_healthy_and_age():
    m = PrometheusMonitor()
    m.refresh_replica_gauges(_snapshot(
        {("t3set", "push"): {"total": 3, "healthy": 2}},
        {"t3set": 45.0},
    ))
    assert _default_registry.get_sample_value(TOTAL, {"worker_type": "t3set", "delivery_mode": "push"}) == 3
    assert _default_registry.get_sample_value(HEALTHY, {"worker_type": "t3set", "delivery_mode": "push"}) == 2
    assert _default_registry.get_sample_value(AGE, {"worker_type": "t3set"}) == 45.0


def test_drained_count_zeroed_next_refresh():
    m = PrometheusMonitor()
    m.refresh_replica_gauges(_snapshot({("t3drain", "push"): {"total": 2, "healthy": 2}}, {"t3drain": 5.0}))
    m.refresh_replica_gauges(_snapshot({}, {}))  # type drained
    assert _default_registry.get_sample_value(TOTAL, {"worker_type": "t3drain", "delivery_mode": "push"}) == 0
    assert _default_registry.get_sample_value(HEALTHY, {"worker_type": "t3drain", "delivery_mode": "push"}) == 0


def test_age_series_removed_when_population_empty():
    m = PrometheusMonitor()
    m.refresh_replica_gauges(_snapshot({("t3age", "push"): {"total": 1, "healthy": 1}}, {"t3age": 12.0}))
    assert _default_registry.get_sample_value(AGE, {"worker_type": "t3age"}) == 12.0
    # next tick: still 1 replica but none fresh -> age None
    m.refresh_replica_gauges(_snapshot({("t3age", "push"): {"total": 1, "healthy": 0}}, {"t3age": None}))
    assert _default_registry.get_sample_value(AGE, {"worker_type": "t3age"}) is None
    # count still present (0 healthy), not removed
    assert _default_registry.get_sample_value(TOTAL, {"worker_type": "t3age", "delivery_mode": "push"}) == 1


def test_age_none_never_sets_zero():
    m = PrometheusMonitor()
    m.refresh_replica_gauges(_snapshot({("t3none", "pull"): {"total": 1, "healthy": 0}}, {"t3none": None}))
    assert _default_registry.get_sample_value(AGE, {"worker_type": "t3none"}) is None
