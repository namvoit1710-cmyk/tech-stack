"""B3 (SA-2028) executor half — the lease endpoint / LeaseTasksUseCase (long-poll).

Direct async use-case tests + one route/envelope test through the real app.
"""

import sys
import os
import asyncio
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer2_application.features.lease_tasks.use_cases.lease_tasks_usecase import (
    LeaseTasksUseCase, LeaseTasksInput,
)
from app.layer2_application.interfaces.ready_task_store_interface import TaskRow
from app.layer4_frameworks.storage.memory_ready_task_store import MemoryReadyTaskStore


class DummyLogger:
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


def _cfg(**kw):
    d = dict(LEASE_TTL_SECONDS=60, LEASE_TTL_OVERRIDES="",
             LEASE_MAX_WAIT_SECONDS=20, LEASE_MAX_BATCH=16)
    d.update(kw)
    return SimpleNamespace(**d)


def _row(task_id, worker_type="pw1", tenant="", **kw):
    return TaskRow(task_id=task_id, run_id=kw.get("run_id", "r1"),
                   node_id=kw.get("node_id", "n1"), worker_type=worker_type,
                   tenant=tenant, payload=kw.get("payload", {}),
                   idempotency_key=kw.get("idempotency_key", ""))


def _uc(store, **cfg):
    return LeaseTasksUseCase(logger=DummyLogger(), ready_task_store=store, app_config=_cfg(**cfg))


# --- gate ---

async def test_disabled_returns_empty():
    uc = LeaseTasksUseCase(logger=DummyLogger(), ready_task_store=None, app_config=_cfg())
    assert await uc.execute(LeaseTasksInput(worker_type="pw1")) == []

async def test_blank_worker_type_returns_empty():
    store = MemoryReadyTaskStore()
    assert await _uc(store).execute(LeaseTasksInput(worker_type="")) == []


# --- claim ---

async def test_leases_ready_tasks():
    store = MemoryReadyTaskStore()
    store.enqueue(_row("t1", payload={"k": "v"}))
    got = await _uc(store).execute(LeaseTasksInput(worker_type="pw1", max_n=5))
    assert [r["task_id"] for r in got] == ["t1"]
    assert got[0]["lease_token"]           # renewal/callback credential
    assert got[0]["deadline"]
    assert got[0]["lease_ttl_seconds"] == 60   # worker derives its renew interval from this (B4)
    assert got[0]["payload"] == {"k": "v"}
    assert store.get("t1").state.value == "LEASED"

async def test_honors_max_n_and_cap():
    store = MemoryReadyTaskStore()
    for i in range(5):
        store.enqueue(_row(f"t{i}"))
    got = await _uc(store).execute(LeaseTasksInput(worker_type="pw1", max_n=2))
    assert len(got) == 2
    # cap: max_n above LEASE_MAX_BATCH is clamped
    got2 = await _uc(store, LEASE_MAX_BATCH=1).execute(LeaseTasksInput(worker_type="pw1", max_n=999))
    assert len(got2) == 1

async def test_partition_isolation():
    store = MemoryReadyTaskStore()
    store.enqueue(_row("a", worker_type="pw1", tenant="t-A"))
    store.enqueue(_row("b", worker_type="pw1", tenant="t-B"))
    got = await _uc(store).execute(LeaseTasksInput(worker_type="pw1", tenant="t-A", max_n=10))
    assert [r["task_id"] for r in got] == ["a"]


# --- long-poll ---

async def test_longpoll_expires_empty():
    store = MemoryReadyTaskStore()
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    got = await _uc(store).execute(LeaseTasksInput(worker_type="pw1", wait_seconds=0.4))
    assert got == []
    assert loop.time() - t0 >= 0.35        # actually waited

async def test_longpoll_returns_when_task_appears():
    store = MemoryReadyTaskStore()
    uc = _uc(store)
    lease = asyncio.create_task(uc.execute(LeaseTasksInput(worker_type="pw1", wait_seconds=3, max_n=5)))
    await asyncio.sleep(0.2)
    store.enqueue(_row("late"))
    got = await lease
    assert [r["task_id"] for r in got] == ["late"]

async def test_no_wait_returns_immediately_empty():
    store = MemoryReadyTaskStore()
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    assert await _uc(store).execute(LeaseTasksInput(worker_type="pw1", wait_seconds=0)) == []
    assert loop.time() - t0 < 0.2          # did not block


# --- per-type TTL ---

async def test_lease_ttl_override_applied():
    from datetime import datetime, timezone, timedelta
    store = MemoryReadyTaskStore()
    fixed = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
    store._now = lambda: fixed          # freeze clock to assert the ACTUAL deadline
    store.enqueue(_row("t1", worker_type="agent"))
    uc = _uc(store, LEASE_TTL_SECONDS=30, LEASE_TTL_OVERRIDES='{"agent": 600}')
    got = await uc.execute(LeaseTasksInput(worker_type="agent", max_n=1))
    # deadline must reflect the 600 override, NOT the 30 default — a real assert,
    # not just "truthy" (which passed even with the wrong ttl).
    assert got[0]["deadline"] == (fixed + timedelta(seconds=600)).isoformat()
    assert uc._lease_ttl_for("agent") == 600
    assert uc._lease_ttl_for("other") == 30


async def test_max_n_clamped_to_min_one():
    store = MemoryReadyTaskStore()
    for i in range(3):
        store.enqueue(_row(f"t{i}"))
    got = await _uc(store).execute(LeaseTasksInput(worker_type="pw1", max_n=-5))
    assert len(got) == 1               # negative/zero clamps up to 1

async def test_negative_wait_returns_immediately():
    store = MemoryReadyTaskStore()
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    assert await _uc(store).execute(LeaseTasksInput(worker_type="pw1", wait_seconds=-10)) == []
    assert loop.time() - t0 < 0.2      # negative wait does not block


# --- route wiring + envelope (TestClient) ---

def test_lease_route_and_envelope():
    from fastapi.testclient import TestClient
    from main import create_app
    from bootstrap import build_app_container
    from app.layer4_frameworks.config.app_config import settings

    container = build_app_container()
    store = MemoryReadyTaskStore()
    store.enqueue(_row("rt1", worker_type="pw1", payload={"hello": "world"}))
    container["lease_tasks"] = LeaseTasksUseCase(
        logger=DummyLogger(), ready_task_store=store, app_config=settings,
    )
    app = create_app()
    app.state.container = container
    client = TestClient(app)

    resp = client.post("/api/v1/workers/type/pw1/tasks/lease?max=5&wait=0",
                       headers={"X-Tenant-Id": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"                    # response envelope
    assert [t["task_id"] for t in body["data"]] == ["rt1"]
    assert body["data"][0]["payload"] == {"hello": "world"}
    # second lease → empty (already leased)
    resp2 = client.post("/api/v1/workers/type/pw1/tasks/lease?max=5&wait=0")
    assert resp2.json()["data"] == []
