"""SA-1530 — WORKER_MAX_CONCURRENT acceptance gate on /execute-async.

Design (sealed 2026-07-02): acquire BEFORE the 202; at capacity respond
429 WORKER_SATURATED + Retry-After (never 503 — saturated is busy, not
broken); release on success AND error paths; WORKER_MAX_CONCURRENT=0
keeps legacy unlimited behaviour byte-identical; snapshot exposed for
the autoscale signal.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from worker_sdk.layer2_application.services.worker_concurrency import WorkerConcurrency
from worker_sdk.layer3_adapters.controllers.restful.v1.routes import create_router
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus


class _SlowUseCase:
    """Blocks until released — holds a concurrency slot deterministically."""

    def __init__(self):
        self.gate = threading.Event()
        self.started = threading.Event()

    def execute(self, input):
        self.started.set()
        self.gate.wait(timeout=10)

        class _Out:
            task_id = input.task_id
            status = TaskStatus.SUCCESS
            outputs = {}
            error = None
            duration_ms = 1.0
            output_reference = ""

        return _Out()


class _BoomUseCase:
    def execute(self, input):
        raise RuntimeError("handler exploded")


def _make_client(exec_uc, max_concurrent):
    container = {
        "execute_task_usecase": exec_uc,
        "_dependencies": {"worker_concurrency": WorkerConcurrency(max_concurrent)},
    }
    app = FastAPI()
    app.include_router(create_router(container), prefix="/api/v1")
    return TestClient(app), container["_dependencies"]["worker_concurrency"]


def _payload(tid="t1"):
    return {"task_id": tid, "action": "execute", "inputs": {}, "parameters": {},
            "callback_url": None}


class TestConcurrencyClass:
    def test_unlimited_when_zero(self):
        c = WorkerConcurrency(0)
        assert not c.limited
        assert all(c.acquire() for _ in range(1000))
        assert c.snapshot()["saturation"] == 0.0

    def test_cap_and_release(self):
        c = WorkerConcurrency(2)
        assert c.acquire() and c.acquire()
        assert not c.acquire()  # full
        c.release()
        assert c.acquire()  # freed slot reusable
        assert c.snapshot() == {"active": 2, "max": 2, "saturation": 1.0}

    def test_race_never_exceeds_cap(self):
        c = WorkerConcurrency(5)
        results = []
        barrier = threading.Barrier(32)

        def go():
            barrier.wait()
            results.append(c.acquire())

        threads = [threading.Thread(target=go) for _ in range(32)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert results.count(True) == 5  # exactly cap, never more
        assert c.snapshot()["active"] == 5


class TestAcceptanceGate:
    def test_under_cap_returns_202(self):
        uc = _SlowUseCase()
        client, conc = _make_client(uc, max_concurrent=2)
        resp = client.post("/api/v1/execute-async", json=_payload())
        assert resp.status_code == 202
        uc.gate.set()

    def test_at_cap_returns_429_with_retry_after(self):
        uc = _SlowUseCase()
        client, conc = _make_client(uc, max_concurrent=1)
        # Fill the single slot manually (deterministic — no bg-task timing).
        assert conc.acquire()
        resp = client.post("/api/v1/execute-async", json=_payload("t2"))
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After") == "2"
        body = resp.json()
        assert body["error"] == "WORKER_SATURATED"
        assert body["active"] == 1 and body["max"] == 1
        uc.gate.set()

    def test_slot_released_after_success(self):
        uc = _SlowUseCase()
        client, conc = _make_client(uc, max_concurrent=1)
        uc.gate.set()  # handler completes immediately
        resp = client.post("/api/v1/execute-async", json=_payload())
        assert resp.status_code == 202
        # TestClient runs background tasks before returning — slot must be free.
        assert conc.snapshot()["active"] == 0

    def test_slot_released_after_handler_error(self):
        client, conc = _make_client(_BoomUseCase(), max_concurrent=1)
        # TestClient re-raises background-task exceptions (legacy behaviour,
        # unchanged) — the invariant under test is that the slot is released
        # by the finally EVEN when the handler explodes.
        try:
            client.post("/api/v1/execute-async", json=_payload())
        except RuntimeError:
            pass
        assert conc.snapshot()["active"] == 0  # finally released

    def test_zero_keeps_legacy_unlimited(self):
        uc = _SlowUseCase()
        client, conc = _make_client(uc, max_concurrent=0)
        uc.gate.set()
        for i in range(5):
            assert client.post(
                "/api/v1/execute-async", json=_payload(f"t{i}"),
            ).status_code == 202


class TestReadyExposesSnapshot:
    def test_ready_payload_includes_concurrency(self):
        from worker_sdk.layer3_adapters.controllers.worker_server import (
            create_worker_app,
        )
        container = {
            "_dependencies": {"worker_concurrency": WorkerConcurrency(4)},
        }
        app = create_worker_app(container)
        client = TestClient(app)
        body = client.get("/ready").json()
        assert body["ready"] is True
        assert body["concurrency"] == {"active": 0, "max": 4, "saturation": 0.0}
