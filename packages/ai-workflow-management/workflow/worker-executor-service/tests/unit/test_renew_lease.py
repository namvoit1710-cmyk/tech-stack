"""B4 (SA-2029) executor half — lease renewal (RenewLeaseUseCase + endpoint)."""

import sys
import os
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer2_application.features.renew_lease.use_cases.renew_lease_usecase import (
    RenewLeaseUseCase, RenewLeaseInput,
)
from app.layer2_application.interfaces.ready_task_store_interface import TaskRow
from app.layer4_frameworks.storage.memory_ready_task_store import MemoryReadyTaskStore


class DummyLogger:
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


def _cfg(**kw):
    d = dict(LEASE_TTL_SECONDS=60, LEASE_TTL_OVERRIDES="")
    d.update(kw)
    return SimpleNamespace(**d)


def _row(task_id, worker_type="pw1"):
    return TaskRow(task_id=task_id, run_id="r1", node_id="n1", worker_type=worker_type)


def _leased_store():
    store = MemoryReadyTaskStore()
    store.enqueue(_row("t1"))
    token = store.claim("pw1", "", 1, lease_ttl_seconds=30)[0].lease_token
    return store, token


def _uc(store, **cfg):
    return RenewLeaseUseCase(logger=DummyLogger(), ready_task_store=store, app_config=_cfg(**cfg))


def test_renew_success_returns_new_deadline():
    store, token = _leased_store()
    r = _uc(store).execute(RenewLeaseInput(task_id="t1", lease_token=token))
    assert r["ok"] is True
    assert r["deadline"] == store.get("t1").deadline

def test_renew_wrong_token_superseded():
    store, _ = _leased_store()
    r = _uc(store).execute(RenewLeaseInput(task_id="t1", lease_token="forged"))
    assert r["ok"] is False and r["code"] == "TASK_SUPERSEDED"

def test_renew_unknown_task_superseded():
    store, token = _leased_store()
    r = _uc(store).execute(RenewLeaseInput(task_id="ghost", lease_token=token))
    assert r["ok"] is False

def test_renew_disabled_or_blank():
    assert _uc(None).execute(RenewLeaseInput(task_id="t1", lease_token="x"))["ok"] is False
    store, token = _leased_store()
    assert _uc(store).execute(RenewLeaseInput(task_id="", lease_token=token))["ok"] is False
    assert _uc(store).execute(RenewLeaseInput(task_id="t1", lease_token=""))["ok"] is False

def test_renew_uses_per_type_ttl():
    from datetime import datetime, timezone, timedelta
    store = MemoryReadyTaskStore()
    fixed = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
    store._now = lambda: fixed
    store.enqueue(_row("t1", worker_type="agent"))
    token = store.claim("agent", "", 1, lease_ttl_seconds=30)[0].lease_token
    uc = _uc(store, LEASE_TTL_SECONDS=30, LEASE_TTL_OVERRIDES='{"agent": 600}')
    r = uc.execute(RenewLeaseInput(task_id="t1", lease_token=token))
    assert r["deadline"] == (fixed + timedelta(seconds=600)).isoformat()   # 600 override


# --- route (TestClient) ---

def test_renew_route_200_and_409():
    from fastapi.testclient import TestClient
    from main import create_app
    from bootstrap import build_app_container
    from app.layer4_frameworks.config.app_config import settings

    store, token = _leased_store()
    container = build_app_container()
    container["renew_lease"] = RenewLeaseUseCase(
        logger=DummyLogger(), ready_task_store=store, app_config=settings,
    )
    app = create_app()
    app.state.container = container
    client = TestClient(app)

    ok = client.post("/api/v1/tasks/t1/lease/renew", headers={"X-Lease-Token": token})
    assert ok.status_code == 200
    assert ok.json()["data"]["deadline"]

    bad = client.post("/api/v1/tasks/t1/lease/renew", headers={"X-Lease-Token": "forged"})
    assert bad.status_code == 409
    assert bad.json()["error"]["code"] == "TASK_SUPERSEDED"
