"""SA-2424 — per-node worker-instance pin (debug routing).

Covers the two BE guarantees:
  1. WorkerDispatcher.get_executor_for_instance resolves a SPECIFIC instance and
     raises InstancePinUnavailableError instead of ever falling back.
  2. ExecuteTaskUseCase fails a pinned task fast (INSTANCE_PIN_UNAVAILABLE,
     non-retryable) when the pin can't be honored, and dispatches to the pinned
     instance's endpoint when it can — while an unpinned task is byte-for-byte
     unchanged.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer1_domain.errors import InstancePinUnavailableError
from app.layer4_frameworks.providers.worker_dispatcher import WorkerDispatcher
from app.layer4_frameworks.repositories.memory_worker_registry_store import (
    MemoryWorkerRegistryStore,
)
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    ExecuteTaskInput,
)


def _reg(instance_id, worker_type, endpoint_url, health="healthy"):
    return WorkerRegistration(
        instance_id=instance_id,
        worker_type=worker_type,
        endpoint_url=endpoint_url,
        health_status=health,
        name=f"{worker_type}@{instance_id}",
    )


# --- WorkerDispatcher.get_executor_for_instance ---------------------------------

def test_pin_resolves_specific_instance_by_instance_id():
    store = MemoryWorkerRegistryStore()
    store.register(_reg("local", "email-worker", "http://localhost:9101"))
    store.register(_reg("server", "email-worker", "http://cf-route:9101"))
    dispatcher = WorkerDispatcher(registry_store=store)

    local = dispatcher.get_executor_for_instance("local", "email-worker")
    server = dispatcher.get_executor_for_instance("server", "email-worker")

    # Each pin lands on ITS OWN endpoint — the whole debug point.
    assert local._worker_url == "http://localhost:9101"
    assert server._worker_url == "http://cf-route:9101"


def test_pin_resolves_by_full_registration_id():
    store = MemoryWorkerRegistryStore()
    store.register(_reg("local", "email-worker", "http://localhost:9101"))
    dispatcher = WorkerDispatcher(registry_store=store)
    # <instance_id>:<worker_type> is the store key
    ex = dispatcher.get_executor_for_instance("local:email-worker", "email-worker")
    assert ex._worker_url == "http://localhost:9101"


def test_pin_unknown_instance_raises():
    store = MemoryWorkerRegistryStore()
    store.register(_reg("local", "email-worker", "http://localhost:9101"))
    dispatcher = WorkerDispatcher(registry_store=store)
    with pytest.raises(InstancePinUnavailableError):
        dispatcher.get_executor_for_instance("ghost", "email-worker")


def test_pin_wrong_worker_type_raises():
    store = MemoryWorkerRegistryStore()
    store.register(_reg("local", "email-worker", "http://localhost:9101"))
    dispatcher = WorkerDispatcher(registry_store=store)
    with pytest.raises(InstancePinUnavailableError):
        dispatcher.get_executor_for_instance("local", "http-request")


def test_pin_unhealthy_instance_raises():
    store = MemoryWorkerRegistryStore()
    store.register(_reg("local", "email-worker", "http://localhost:9101", health="unhealthy"))
    dispatcher = WorkerDispatcher(registry_store=store)
    with pytest.raises(InstancePinUnavailableError):
        dispatcher.get_executor_for_instance("local", "email-worker")


def test_pin_no_endpoint_raises():
    store = MemoryWorkerRegistryStore()
    store.register(_reg("local", "email-worker", ""))
    dispatcher = WorkerDispatcher(registry_store=store)
    with pytest.raises(InstancePinUnavailableError):
        dispatcher.get_executor_for_instance("local", "email-worker")


def test_pin_no_registry_raises():
    dispatcher = WorkerDispatcher()
    with pytest.raises(InstancePinUnavailableError):
        dispatcher.get_executor_for_instance("local", "email-worker")


def test_pin_by_host_fallback_resolves_when_instance_id_stale():
    # SA-2437: instance_id churned (uuid changed) but the same machine (host) is
    # still registered -> host fallback routes to it.
    store = MemoryWorkerRegistryStore()
    store.register(_reg("uuid-new", "email-worker", "http://localhost:9101"))  # host blank
    # give it a host by re-registering with host set
    w = WorkerRegistration(instance_id="uuid-new", worker_type="email-worker",
                           endpoint_url="http://localhost:9101", host="DESKTOP-1")
    store.register(w)
    dispatcher = WorkerDispatcher(registry_store=store)
    ex = dispatcher.get_executor_for_instance("stale-old-uuid", "email-worker", "DESKTOP-1")
    assert ex._worker_url == "http://localhost:9101"


def test_pin_by_host_unknown_host_raises():
    store = MemoryWorkerRegistryStore()
    w = WorkerRegistration(instance_id="i1", worker_type="email-worker",
                           endpoint_url="http://localhost:9101", host="DESKTOP-1")
    store.register(w)
    dispatcher = WorkerDispatcher(registry_store=store)
    with pytest.raises(InstancePinUnavailableError):
        dispatcher.get_executor_for_instance("", "email-worker", "OTHER-HOST")


def test_unpinned_get_executor_still_freshest_healthy():
    # No pin path is unchanged: still resolves by worker_type.
    store = MemoryWorkerRegistryStore()
    store.register(_reg("local", "email-worker", "http://localhost:9101"))
    dispatcher = WorkerDispatcher(registry_store=store)
    ex = dispatcher.get_executor("email-worker")
    assert ex is not None and ex._worker_url == "http://localhost:9101"


# --- ExecuteTaskUseCase fail-fast / honor-pin -----------------------------------

class _Logger:
    def info(self, *a, **k): pass
    def error(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def debug(self, *a, **k): pass


class _Publisher:
    def __init__(self): self.published = []
    def publish_result(self, r): self.published.append(r)


class _Reporter:
    def __init__(self): self.failed_calls = []; self.completed_calls = []
    def report_failed(self, **k): self.failed_calls.append(k)
    def report_completed(self, **k): self.completed_calls.append(k)


class _Executor:
    def __init__(self, result): self._result = result; self.last = None
    def execute(self, payload): self.last = payload; return self._result


class _PinDispatcher:
    """Honors a pin only for the configured (instance_id -> executor); anything
    else raises, exactly like the real dispatcher."""
    def __init__(self, by_instance):
        self._by_instance = by_instance
    def get_executor(self, worker_type):
        # unpinned path — return a default healthy executor
        return _Executor({"status": "completed", "outputs": {}})
    def get_executor_for_instance(self, target_instance_id, worker_type="", target_host=""):
        ex = self._by_instance.get(target_instance_id) or self._by_instance.get(target_host)
        if ex is None:
            raise InstancePinUnavailableError(target_instance_id or target_host, worker_type, "not registered")
        return ex


def _uc(dispatcher, reporter=None, publisher=None):
    return ExecuteTaskUseCase(
        logger=_Logger(),
        task_queue=object(),
        result_publisher=publisher or _Publisher(),
        decision_switch=object(),
        worker_dispatcher=dispatcher,
        result_reporter=reporter,
    )


def test_execute_pinned_task_fails_fast_when_unavailable():
    reporter = _Reporter()
    uc = _uc(_PinDispatcher(by_instance={}), reporter=reporter)
    out = uc.execute(ExecuteTaskInput(
        task_id="t1", run_id="r1", node_id="n1",
        worker_type="email-worker", target_instance_id="ghost",
    ))
    assert out["status"] == "FAILED"
    assert out["error"]["code"] == "INSTANCE_PIN_UNAVAILABLE"
    assert out["error"]["retryable"] is False
    # WCP was told, via the reporter, that the task failed (no silent fallback).
    assert len(reporter.failed_calls) == 1
    assert reporter.failed_calls[0]["error"]["code"] == "INSTANCE_PIN_UNAVAILABLE"


def test_execute_pinned_task_dispatches_to_pinned_instance():
    ex = _Executor({"status": "completed", "outputs": {"uri": "s3://out"}})
    reporter = _Reporter()
    uc = _uc(_PinDispatcher(by_instance={"local": ex}), reporter=reporter)
    out = uc.execute(ExecuteTaskInput(
        task_id="t2", run_id="r2", node_id="n2",
        worker_type="email-worker", target_instance_id="local",
    ))
    assert out["status"] == "COMPLETED"
    # It ran on the pinned executor.
    assert ex.last is not None and ex.last["task_id"] == "t2"


def test_execute_unpinned_task_unaffected():
    uc = _uc(_PinDispatcher(by_instance={}))
    out = uc.execute(ExecuteTaskInput(
        task_id="t3", run_id="r3", node_id="n3", worker_type="email-worker",
    ))
    assert out["status"] == "COMPLETED"
