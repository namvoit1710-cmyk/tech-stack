"""B9 (SA-2034) — observability + autoscale: queue-stats (scale-from-zero signal),
per-partition stats, real metrics."""

import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer2_application.interfaces.ready_task_store_interface import TaskRow
from app.layer4_frameworks.storage.memory_ready_task_store import MemoryReadyTaskStore
from app.layer2_application.features.queue_stats.use_cases.queue_stats_usecase import (
    QueueStatsUseCase, QueueStatsInput,
)


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass


def _clocked():
    store = MemoryReadyTaskStore()
    clock = {"t": datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)}
    store._now = lambda: clock["t"]
    return store, clock

def _row(tid, wt="pw1", tenant="t-A"):
    return TaskRow(task_id=tid, run_id="r1", node_id="n1", worker_type=wt, tenant=tenant)


# ======================================================================
# store.queue_stats
# ======================================================================

def test_queue_stats_admitted_depth_and_leased():
    store, _ = _clocked()
    for i in range(3):
        store.enqueue(_row(f"t{i}"))
    store.claim("pw1", "t-A", 1, 30)                     # one leased
    s = store.queue_stats("pw1", "t-A")
    assert s["admitted_depth"] == 2                      # 3 - 1 leased
    assert s["leased"] == 1
    assert s["worker_type"] == "pw1" and s["tenant"] == "t-A"

def test_queue_stats_partition_isolation():
    store, _ = _clocked()
    store.enqueue(_row("a", wt="pw1", tenant="t-A"))
    store.enqueue(_row("b", wt="pw1", tenant="t-B"))
    store.enqueue(_row("c", wt="pw2", tenant="t-A"))
    assert store.queue_stats("pw1", "t-A")["admitted_depth"] == 1
    assert store.queue_stats("pw1", "t-B")["admitted_depth"] == 1
    assert store.queue_stats("pw2", "t-A")["admitted_depth"] == 1
    assert store.queue_stats("pw9", "t-A")["admitted_depth"] == 0   # empty partition

def test_queue_stats_oldest_ready_age():
    store, clock = _clocked()
    store.enqueue(_row("t1"))
    clock["t"] += timedelta(seconds=42)
    s = store.queue_stats("pw1", "t-A")
    assert abs(s["oldest_ready_age_seconds"] - 42.0) < 0.01

def test_stats_all_covers_every_partition():
    store, _ = _clocked()
    store.enqueue(_row("a", wt="pw1", tenant="t-A"))
    store.enqueue(_row("b", wt="pw2", tenant="t-B"))
    partitions = {(s["worker_type"], s["tenant"]) for s in store.stats_all()}
    assert partitions == {("pw1", "t-A"), ("pw2", "t-B")}

def test_admitted_depth_excludes_not_yet_visible():
    """A backed-off (future visible_at) READY row is NOT leasable → must NOT inflate
    the autoscale signal (else KEDA scales up for work no worker can lease)."""
    store, clock = _clocked()
    store.enqueue(_row("t1"))
    store.claim("pw1", "t-A", 1, lease_ttl_seconds=1)     # lease it
    clock["t"] += timedelta(seconds=2)                    # expire
    store.sweep_expired_leases(max_attempts=5, backoff_base_seconds=10)  # re-enqueue, visible in +10s
    # right now the row is READY but backed off → not leasable → depth 0
    assert store.queue_stats("pw1", "t-A")["admitted_depth"] == 0
    assert store.stats_all()[0]["admitted_depth"] == 0
    clock["t"] += timedelta(seconds=11)                   # backoff elapsed → visible
    assert store.queue_stats("pw1", "t-A")["admitted_depth"] == 1

def test_refresh_pull_gauges_zeros_drained_partition():
    from app.layer4_frameworks.monitoring import prometheus as pm
    mon = pm.PrometheusMonitor()
    mon.refresh_pull_gauges([{"worker_type": "pw1", "tenant": "t-A", "admitted_depth": 4, "leased": 1}])
    text = mon.metrics_output()[0].decode()
    assert 'wes_pull_admitted_depth{tenant="t-A",worker_type="pw1"} 4' in text
    # next sweep: partition drained (absent from stats) → gauge must go to 0
    mon.refresh_pull_gauges([])
    text2 = mon.metrics_output()[0].decode()
    assert 'wes_pull_admitted_depth{tenant="t-A",worker_type="pw1"} 0' in text2


# ======================================================================
# QueueStatsUseCase + endpoint
# ======================================================================

def test_usecase_returns_stats():
    store, _ = _clocked()
    store.enqueue(_row("t1"))
    uc = QueueStatsUseCase(logger=_Log(), ready_task_store=store)
    s = uc.execute(QueueStatsInput(worker_type="pw1", tenant="t-A"))
    assert s["admitted_depth"] == 1

def test_usecase_zeroed_when_disabled():
    uc = QueueStatsUseCase(logger=_Log(), ready_task_store=None)
    s = uc.execute(QueueStatsInput(worker_type="pw1", tenant="t-A"))
    assert s == {"worker_type": "pw1", "tenant": "t-A",
                 "admitted_depth": 0, "leased": 0, "oldest_ready_age_seconds": 0.0, "bytes": 0}

def test_queue_stats_route():
    from fastapi.testclient import TestClient
    from main import create_app
    from bootstrap import build_app_container

    store = MemoryReadyTaskStore()
    store.enqueue(_row("t1", wt="pw1", tenant="t-A"))
    store.enqueue(_row("t2", wt="pw1", tenant="t-A"))
    container = build_app_container()
    container["queue_stats"] = QueueStatsUseCase(logger=_Log(), ready_task_store=store)
    app = create_app()
    app.state.container = container
    client = TestClient(app)
    resp = client.get("/api/v1/workers/type/pw1/queue-stats", headers={"X-Tenant-Id": "t-A"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["admitted_depth"] == 2
    assert data["worker_type"] == "pw1"

    # A ?tenant= query param is NOT honored (header-only) → can't read t-A's backlog
    # by naming it without the gateway-injected header.
    spoof = client.get("/api/v1/workers/type/pw1/queue-stats?tenant=t-A")
    assert spoof.json()["data"]["admitted_depth"] == 0     # tenant resolved to "" not t-A


# ======================================================================
# metrics: increment is real, gauge refresh
# ======================================================================

def test_prometheus_increment_is_real():
    from prometheus_client import CollectorRegistry
    from app.layer4_frameworks.monitoring import prometheus as pm
    mon = pm.PrometheusMonitor()
    mon.increment("pull_admission_full")
    mon.increment("tasks_leased", 3)
    body, _ = mon.metrics_output()
    text = body.decode()
    assert 'wes_events_total{event="pull_admission_full"}' in text
    assert 'wes_events_total{event="tasks_leased"}' in text

def test_set_pull_queue_stats_gauge():
    from app.layer4_frameworks.monitoring import prometheus as pm
    mon = pm.PrometheusMonitor()
    mon.set_pull_queue_stats("pw1", "t-A", admitted_depth=5, leased=2)
    text = mon.metrics_output()[0].decode()
    assert 'wes_pull_admitted_depth{tenant="t-A",worker_type="pw1"} 5' in text
    assert 'wes_pull_leased{tenant="t-A",worker_type="pw1"} 2' in text
