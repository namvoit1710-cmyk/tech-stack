"""B6 (SA-2031) — admission + resource-reservation: byte-quota hierarchy,
connection cap, registry cap."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer2_application.interfaces.ready_task_store_interface import (
    TaskRow, ENQUEUE_OK, ENQUEUE_DUPLICATE, ENQUEUE_ADMISSION_FULL,
)
from app.layer4_frameworks.storage.memory_ready_task_store import MemoryReadyTaskStore
from app.layer4_frameworks.repositories.memory_worker_registry_store import MemoryWorkerRegistryStore
from app.layer1_domain.entities.worker_registration import WorkerRegistration


def _row(tid, tenant="t-A", wt="pw1", payload=None):
    return TaskRow(task_id=tid, run_id="r", node_id="n", worker_type=wt, tenant=tenant,
                   payload=payload if payload is not None else {"d": "x" * 80})


_ROW_BYTES = MemoryReadyTaskStore._estimate_bytes(_row("probe"))   # per-row byte size


# ======================================================================
# Byte-quota hierarchy
# ======================================================================

def test_partition_byte_quota_rejects_overflow():
    store = MemoryReadyTaskStore(max_bytes_per_partition=_ROW_BYTES * 2 + 1)   # room for 2
    assert store.enqueue(_row("t1")) == ENQUEUE_OK
    assert store.enqueue(_row("t2")) == ENQUEUE_OK
    assert store.enqueue(_row("t3")) == ENQUEUE_ADMISSION_FULL     # 3rd overflows
    assert store.count() == 2

def test_tenant_quota_spans_partitions():
    # tenant cap = room for 2 rows total across the tenant's partitions
    store = MemoryReadyTaskStore(max_bytes_per_tenant=_ROW_BYTES * 2 + 1)
    assert store.enqueue(_row("a", tenant="t-A", wt="pw1")) == ENQUEUE_OK
    assert store.enqueue(_row("b", tenant="t-A", wt="pw2")) == ENQUEUE_OK   # diff partition, same tenant
    assert store.enqueue(_row("c", tenant="t-A", wt="pw3")) == ENQUEUE_ADMISSION_FULL
    # a DIFFERENT tenant is unaffected
    assert store.enqueue(_row("d", tenant="t-B", wt="pw1")) == ENQUEUE_OK

def test_global_quota_spans_tenants():
    store = MemoryReadyTaskStore(max_bytes_global=_ROW_BYTES * 2 + 1)
    assert store.enqueue(_row("a", tenant="t-A")) == ENQUEUE_OK
    assert store.enqueue(_row("b", tenant="t-B")) == ENQUEUE_OK
    assert store.enqueue(_row("c", tenant="t-C")) == ENQUEUE_ADMISSION_FULL

def test_unlimited_by_default():
    store = MemoryReadyTaskStore()   # no quotas
    for i in range(50):
        assert store.enqueue(_row(f"t{i}")) == ENQUEUE_OK
    assert store.count() == 50

def test_duplicate_not_double_counted():
    store = MemoryReadyTaskStore(max_bytes_per_partition=_ROW_BYTES * 2 + 1)
    assert store.enqueue(_row("t1")) == ENQUEUE_OK
    assert store.enqueue(_row("t1")) == ENQUEUE_DUPLICATE   # same id — no extra bytes
    assert store.enqueue(_row("t2")) == ENQUEUE_OK          # still room for a real 2nd


# --- accounting freed when a row leaves ---

def test_bytes_freed_on_complete():
    store = MemoryReadyTaskStore(max_bytes_per_partition=_ROW_BYTES + 1)   # room for exactly 1
    store.enqueue(_row("t1"))
    assert store.enqueue(_row("t2")) == ENQUEUE_ADMISSION_FULL   # full
    tok = store.claim("pw1", "t-A", 1, 30)[0].lease_token
    store.complete("t1", tok, tenant="t-A", worker_type="pw1")   # frees the bytes
    assert store.enqueue(_row("t2")) == ENQUEUE_OK               # room again

def test_bytes_freed_on_sweep_stale(monkeypatch):
    from datetime import datetime, timezone, timedelta
    store = MemoryReadyTaskStore(max_bytes_per_partition=_ROW_BYTES + 1)
    clock = {"t": datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)}
    store._now = lambda: clock["t"]
    store.enqueue(_row("t1"))
    assert store.enqueue(_row("t2")) == ENQUEUE_ADMISSION_FULL
    clock["t"] = clock["t"] + timedelta(seconds=3600)
    store.sweep_stale_ready(max_ready_seconds=600)               # removes t1, frees bytes
    assert store.enqueue(_row("t2")) == ENQUEUE_OK


# ======================================================================
# Connection cap (LeaseTasksUseCase)
# ======================================================================

@pytest.mark.asyncio
async def test_lease_connection_cap_sheds():
    from types import SimpleNamespace
    from app.layer2_application.features.lease_tasks.use_cases.lease_tasks_usecase import (
        LeaseTasksUseCase, LeaseTasksInput,
    )
    class _L:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
    store = MemoryReadyTaskStore()
    store.enqueue(_row("t1"))
    cfg = SimpleNamespace(LEASE_TTL_SECONDS=60, LEASE_TTL_OVERRIDES="",
                          LEASE_MAX_WAIT_SECONDS=20, LEASE_MAX_BATCH=16,
                          MAX_TOTAL_LEASED=0, MAX_LEASE_CONNECTIONS=1)
    uc = LeaseTasksUseCase(logger=_L(), ready_task_store=store, app_config=cfg)
    uc._active_polls[("t-A", "pw1")] = 1       # simulate one long-poll already held
    assert await uc.execute(LeaseTasksInput(worker_type="pw1", tenant="t-A", max_n=5)) == []
    # freed → next lease works
    uc._active_polls.pop(("t-A", "pw1"), None)
    got = await uc.execute(LeaseTasksInput(worker_type="pw1", tenant="t-A", max_n=5))
    assert [t["task_id"] for t in got] == ["t1"]


# ======================================================================
# Registry cap (REGISTRY_UNBOUNDED)
# ======================================================================

def _reg(wt, hb):
    return WorkerRegistration(worker_type=wt, name=wt, last_heartbeat=hb)

def _capped_store(**kw):
    from datetime import datetime, timezone
    store = MemoryWorkerRegistryStore(**kw)
    store._now = lambda: datetime(2026, 7, 15, 13, 0, 0, tzinfo=timezone.utc)   # fixed "now"
    return store

def test_registry_cap_evicts_stale():
    # heartbeats an hour old → all STALE (default window 120s) → w1 (stalest) evicted
    store = _capped_store(max_registered=2)
    store.register(_reg("w1", "2026-07-15T12:00:00+00:00"))
    store.register(_reg("w2", "2026-07-15T12:01:00+00:00"))
    store.register(_reg("w3", "2026-07-15T12:02:00+00:00"))
    assert {w.worker_type for w in store.list_all()} == {"w2", "w3"}

def test_registry_never_evicts_live_worker():
    """The attack: a flood of fresh registrations must NOT oust a live victim.
    When every slot holds a live (recently-heartbeated) worker, the NEW one is
    refused instead of evicting a victim."""
    store = _capped_store(max_registered=2)
    store.register(_reg("victim1", "2026-07-15T12:59:30+00:00"))   # 30s ago → live
    store.register(_reg("victim2", "2026-07-15T12:59:45+00:00"))   # live
    store.register(_reg("attacker", "2026-07-15T13:00:00+00:00"))  # would evict — refused
    types = {w.worker_type for w in store.list_all()}
    assert types == {"victim1", "victim2"}     # victims safe
    assert "attacker" not in types             # excess registration rejected

def test_registry_upsert_does_not_grow():
    """SA-2049: the dedupe key is the REGISTRATION (`<instance_id>:<worker_type>`),
    not worker_type. Re-registering the SAME (process, type) pair -- a pod restart --
    overwrites its own row in place and must not count as new growth against the cap.

    The store is seeded to EXACTLY the cap with two LIVE rows first, so the cap's
    `len(self._store) >= self._max_registered` arm is TRUE and only the
    `worker.id not in self._store` guard admits the restart. Without that guard the
    restart would be REFUSED -- both rows are live, so nothing is evictable and
    register() would `return`, leaving the restarted worker's row stale forever.

    (As previously written this registered one instance twice, peaked at 1 row
    against a cap of 2, and never entered the cap branch at all -- it passed
    identically with the cap disabled via max_registered=0.)
    """
    store = _capped_store(max_registered=2)
    store.register(WorkerRegistration(
        instance_id="w1-instance", worker_type="w1", name="w1",
        last_heartbeat="2026-07-15T12:59:00+00:00",
    ))
    store.register(WorkerRegistration(
        instance_id="w2-instance", worker_type="w2", name="w2",
        last_heartbeat="2026-07-15T12:59:30+00:00",
    ))
    assert len(store.list_all()) == 2                    # at the cap, both live

    # A pod restart: the SAME (process, type) pair re-registers.
    store.register(WorkerRegistration(
        instance_id="w1-instance", worker_type="w1", name="w1-restarted",
        last_heartbeat="2026-07-15T13:00:00+00:00",
    ))
    assert len(store.list_all()) == 2                          # admitted, no growth
    assert store.get("w1-instance:w1").name == "w1-restarted"  # second wins

    # Contrast -- this is what the cap branch is FOR: a NEW pair, every row live,
    # nothing evictable -> refused (backpressure). With the cap off this would be 3.
    store.register(WorkerRegistration(
        instance_id="w3-instance", worker_type="w3", name="w3",
        last_heartbeat="2026-07-15T13:00:00+00:00",
    ))
    assert len(store.list_all()) == 2
    assert {w.worker_type for w in store.list_all()} == {"w1", "w2"}

def test_registry_unlimited_by_default():
    store = _capped_store()
    for i in range(10):
        store.register(_reg(f"w{i}", "2026-07-15T12:00:00+00:00"))
    assert len(store.list_all()) == 10


# ======================================================================
# Byte-estimate hardening + accounting drift
# ======================================================================

def test_estimate_charges_container_overhead():
    """Many tiny nested containers must cost MORE than their raw json length
    (they hold real dict/list memory a json-length proxy under-counts)."""
    nested = {f"k{i}": {} for i in range(20)}     # 20 tiny dicts
    import json as _json
    raw = len(_json.dumps(nested)) + len(_json.dumps({}))
    est = MemoryReadyTaskStore._estimate_bytes(_row("x", payload=nested, tenant="t", wt="pw1"))
    assert est > raw                               # overhead applied

def test_unmeasurable_fails_closed():
    class Boom:
        def __repr__(self): raise RuntimeError("no")
    # json.dumps(default=str) will call str() -> repr -> raises -> fail closed
    row = _row("x", payload={"bad": Boom()})
    assert MemoryReadyTaskStore._estimate_bytes(row) == MemoryReadyTaskStore._UNMEASURABLE_BYTES

def test_accounting_no_drift_over_cycles():
    """Enqueue → lease → expire → re-enqueue → complete, many times: byte counters
    return to zero (no positive leak, no negative drift)."""
    from datetime import datetime, timezone, timedelta
    store = MemoryReadyTaskStore(max_bytes_global=_ROW_BYTES * 3)
    clock = {"t": datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)}
    store._now = lambda: clock["t"]
    for i in range(20):
        assert store.enqueue(_row(f"c{i}")) == ENQUEUE_OK
        tok = store.claim("pw1", "t-A", 1, lease_ttl_seconds=5)[0].lease_token
        clock["t"] = clock["t"] + timedelta(seconds=6)       # lease expires
        store.sweep_expired_leases()                          # → READY (keeps bytes)
        tok2 = store.claim("pw1", "t-A", 1, lease_ttl_seconds=5)[0].lease_token
        store.complete(f"c{i}", tok2, tenant="t-A", worker_type="pw1")  # frees bytes
    assert store.count() == 0
    assert store._bytes_total == 0
    assert store._bytes_by_tenant == {}                      # no empty-key leak
    assert store._bytes_by_partition == {}


@pytest.mark.asyncio
async def test_connection_cap_is_per_partition():
    """One partition at its connection cap must NOT shed a different partition."""
    from types import SimpleNamespace
    from app.layer2_application.features.lease_tasks.use_cases.lease_tasks_usecase import (
        LeaseTasksUseCase, LeaseTasksInput,
    )
    class _L:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
    store = MemoryReadyTaskStore()
    store.enqueue(_row("b", tenant="t-B", wt="pw1"))
    cfg = SimpleNamespace(LEASE_TTL_SECONDS=60, LEASE_TTL_OVERRIDES="",
                          LEASE_MAX_WAIT_SECONDS=20, LEASE_MAX_BATCH=16,
                          MAX_TOTAL_LEASED=0, MAX_LEASE_CONNECTIONS=1)
    uc = LeaseTasksUseCase(logger=_L(), ready_task_store=store, app_config=cfg)
    uc._active_polls[("t-A", "pw1")] = 1               # t-A partition is at cap
    got = await uc.execute(LeaseTasksInput(worker_type="pw1", tenant="t-B", max_n=5))
    assert [t["task_id"] for t in got] == ["b"]        # t-B unaffected
