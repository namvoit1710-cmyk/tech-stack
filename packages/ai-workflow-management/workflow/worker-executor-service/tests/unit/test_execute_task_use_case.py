import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import ExecuteTaskUseCase, ExecuteTaskInput
from app.layer4_frameworks.storage.memory_pending_task_store import MemoryPendingTaskStore
from app.layer2_application.services.concurrency_control import ConcurrencyControl


class DummyLogger:
    def __init__(self):
        self.events = []

    def info(self, message: str, *args, **kwargs):
        self.events.append(("info", message, kwargs))

    def error(self, message: str, *args, **kwargs):
        self.events.append(("error", message, kwargs))

    def warning(self, message: str, *args, **kwargs):
        self.events.append(("warning", message, kwargs))

    def debug(self, message: str, *args, **kwargs):
        self.events.append(("debug", message, kwargs))


class DummyResultPublisher:
    def __init__(self):
        self.published = []

    def publish_result(self, result):
        self.published.append(result)


class DummyTaskQueue:
    pass


class DummyDecisionSwitch:
    pass


class DummyResultReporter:
    def __init__(self, *, fail_on_completed=False, fail_on_failed=False):
        self.completed_calls = []
        self.failed_calls = []
        self._fail_on_completed = fail_on_completed
        self._fail_on_failed = fail_on_failed

    def report_completed(self, **kwargs):
        self.completed_calls.append(kwargs)
        if self._fail_on_completed:
            raise RuntimeError("report_completed failed")

    def report_failed(self, **kwargs):
        self.failed_calls.append(kwargs)
        if self._fail_on_failed:
            raise RuntimeError("report_failed failed")


class DummyExecutor:
    def __init__(self, result=None, *, raises=None, dispatch_raises=None):
        self._result = result
        self._raises = raises
        self._dispatch_raises = dispatch_raises
        self.last_payload = None
        self.last_dispatch_payload = None

    def execute(self, payload):
        self.last_payload = payload
        if self._raises:
            raise self._raises
        return self._result

    def dispatch(self, payload):
        self.last_dispatch_payload = payload
        if self._dispatch_raises:
            raise self._dispatch_raises
        return {"task_id": payload.get("task_id", ""), "accepted": True}


class DummyWorkerDispatcher:
    def __init__(self, executor=None):
        self._executor = executor

    def get_executor(self, worker_id):
        return self._executor


def _make_uc(**overrides):
    defaults = dict(
        logger=DummyLogger(),
        task_queue=DummyTaskQueue(),
        result_publisher=DummyResultPublisher(),
        decision_switch=DummyDecisionSwitch(),
    )
    defaults.update(overrides)
    return ExecuteTaskUseCase(**defaults)


# --- Happy path ---

def test_execute_task_returns_completed():
    publisher = DummyResultPublisher()
    uc = _make_uc(result_publisher=publisher)
    result = uc.execute(ExecuteTaskInput(task_id="t1", run_id="r1", node_id="n1"))
    assert result["status"] == "COMPLETED"
    assert result["task_id"] == "t1"
    assert len(publisher.published) == 1


def test_execute_task_without_worker_dispatcher():
    uc = _make_uc()
    result = uc.execute(ExecuteTaskInput(task_id="t2"))
    assert result["status"] == "COMPLETED"


# --- Worker dispatcher path ---

def test_execute_task_with_worker_dispatcher_returns_worker_output():
    worker_result = {
        "output": {"uri": "s3://bucket/result.json", "output_metadata": {"rows": 42}},
        "metrics": {"duration_ms": 150},
    }
    dispatcher = DummyWorkerDispatcher(executor=DummyExecutor(result=worker_result))
    uc = _make_uc(worker_dispatcher=dispatcher)
    result = uc.execute(ExecuteTaskInput(task_id="t3", worker_type="w1", run_id="r1"))
    assert result["status"] == "COMPLETED"
    assert result["output_reference"] == "s3://bucket/result.json"
    assert result["output_metadata"] == {"rows": 42}
    assert result["execution_time_ms"] == 150


def test_execute_task_sends_correct_payload_to_executor():
    worker_result = {
        "output": {"uri": "s3://bucket/result.json", "output_metadata": {}},
        "metrics": {"duration_ms": 50},
    }
    dummy_executor = DummyExecutor(result=worker_result)
    dispatcher = DummyWorkerDispatcher(executor=dummy_executor)
    uc = _make_uc(worker_dispatcher=dispatcher)
    result = uc.execute(ExecuteTaskInput(
        task_id="t-payload",
        run_id="r-payload",
        worker_type="w1",
        input={"wait_type": "manual_approval", "reason": "Manager sign-off"},
        node_config={"timeout": 60},
    ))
    assert result["status"] == "COMPLETED"
    sent = dummy_executor.last_payload
    assert sent["task_id"] == "t-payload"
    assert sent["action"] == "execute"
    assert sent["inputs"] == {"wait_type": "manual_approval", "reason": "Manager sign-off"}
    assert sent["parameters"] == {"timeout": 60}
    assert sent["correlation_id"] == "r-payload"


def test_execute_task_dispatcher_no_executor():
    dispatcher = DummyWorkerDispatcher(executor=None)
    uc = _make_uc(worker_dispatcher=dispatcher)
    result = uc.execute(ExecuteTaskInput(task_id="t4", worker_type="w1"))
    assert result["status"] == "COMPLETED"
    assert result["output_metadata"] == {}


# --- Concurrency limit ---

def test_execute_task_concurrency_limit():
    uc = _make_uc()
    uc._concurrency.max_concurrent = 0  # force acquire() to always fail
    result = uc.execute(ExecuteTaskInput(task_id="t5", run_id="r1", node_id="n1"))
    assert result["status"] == "FAILED"
    assert result["error"]["code"] == "CONCURRENCY_LIMIT"


# --- Result reporter ---

def test_execute_task_calls_result_reporter_on_success():
    reporter = DummyResultReporter()
    uc = _make_uc(result_reporter=reporter)
    result = uc.execute(ExecuteTaskInput(task_id="t6", run_id="r1"))
    assert result["status"] == "COMPLETED"
    assert len(reporter.completed_calls) == 1
    assert reporter.completed_calls[0]["run_id"] == "r1"


def test_execute_task_result_reporter_failure_does_not_break():
    reporter = DummyResultReporter(fail_on_completed=True)
    logger = DummyLogger()
    uc = _make_uc(logger=logger, result_reporter=reporter)
    result = uc.execute(ExecuteTaskInput(task_id="t7", run_id="r1"))
    assert result["status"] == "COMPLETED"
    error_events = [e for e in logger.events if e[0] == "error"]
    assert any("Failed to report task result to WCP" in e[1] for e in error_events)


# --- Error handling path ---

def test_execute_task_worker_error_returns_failed():
    dispatcher = DummyWorkerDispatcher(
        executor=DummyExecutor(raises=RuntimeError("worker crashed"))
    )
    publisher = DummyResultPublisher()
    uc = _make_uc(worker_dispatcher=dispatcher, result_publisher=publisher)
    result = uc.execute(ExecuteTaskInput(task_id="t8", run_id="r1", worker_type="w1"))
    assert result["status"] == "FAILED"
    assert result["error"]["code"] == "WORKER_ERROR"
    assert result["error"]["message"] == "worker crashed"
    assert len(publisher.published) == 1
    assert publisher.published[0]["message_type"] == "task.failed"


def test_execute_task_error_with_result_reporter():
    dispatcher = DummyWorkerDispatcher(
        executor=DummyExecutor(raises=RuntimeError("boom"))
    )
    reporter = DummyResultReporter()
    uc = _make_uc(worker_dispatcher=dispatcher, result_reporter=reporter)
    result = uc.execute(ExecuteTaskInput(task_id="t9", run_id="r1", worker_type="w1"))
    assert result["status"] == "FAILED"
    assert len(reporter.failed_calls) == 1
    assert reporter.failed_calls[0]["run_id"] == "r1"


def test_execute_task_error_reporter_failure_does_not_break():
    dispatcher = DummyWorkerDispatcher(
        executor=DummyExecutor(raises=RuntimeError("boom"))
    )
    reporter = DummyResultReporter(fail_on_failed=True)
    logger = DummyLogger()
    uc = _make_uc(
        logger=logger,
        worker_dispatcher=dispatcher,
        result_reporter=reporter,
    )
    result = uc.execute(ExecuteTaskInput(task_id="t10", run_id="r1", worker_type="w1"))
    assert result["status"] == "FAILED"
    error_events = [e for e in logger.events if e[0] == "error"]
    assert any("Failed to report failure to WCP" in e[1] for e in error_events)


# --- Async dispatch path ---

def test_async_dispatch_returns_dispatched():
    executor = DummyExecutor()
    dispatcher = DummyWorkerDispatcher(executor=executor)
    pending_store = MemoryPendingTaskStore()
    cc = ConcurrencyControl()

    uc = _make_uc(
        worker_dispatcher=dispatcher,
        pending_task_store=pending_store,
        concurrency_control=cc,
    )
    result = uc.execute(ExecuteTaskInput(
        task_id="t-async", run_id="r1", node_id="n1", worker_type="w1",
        payload={"key": "val"},
    ))

    assert result["status"] == "DISPATCHED"
    assert result["task_id"] == "t-async"
    assert pending_store.get("t-async") is not None
    assert cc.active_count == 1  # slot held until callback
    assert executor.last_dispatch_payload is not None
    assert executor.last_dispatch_payload["task_id"] == "t-async"
    assert "callback_url" in executor.last_dispatch_payload


def test_async_dispatch_failure_cleans_up():
    executor = DummyExecutor(dispatch_raises=RuntimeError("connection refused"))
    dispatcher = DummyWorkerDispatcher(executor=executor)
    pending_store = MemoryPendingTaskStore()
    cc = ConcurrencyControl()

    uc = _make_uc(
        worker_dispatcher=dispatcher,
        pending_task_store=pending_store,
        concurrency_control=cc,
    )
    result = uc.execute(ExecuteTaskInput(
        task_id="t-fail", run_id="r1", node_id="n1", worker_type="w1",
    ))

    assert result["status"] == "FAILED"
    assert pending_store.get("t-fail") is None  # cleaned up
    assert cc.active_count == 0  # slot released


def test_no_pending_store_falls_back_to_sync():
    worker_result = {
        "output": {"uri": "s3://sync", "output_metadata": {"x": 1}},
        "metrics": {"duration_ms": 99},
    }
    executor = DummyExecutor(result=worker_result)
    dispatcher = DummyWorkerDispatcher(executor=executor)

    uc = _make_uc(worker_dispatcher=dispatcher)  # no pending_task_store
    result = uc.execute(ExecuteTaskInput(
        task_id="t-sync", run_id="r1", node_id="n1", worker_type="w1",
    ))

    assert result["status"] == "COMPLETED"
    assert result["output_reference"] == "s3://sync"
    assert executor.last_payload is not None  # sync .execute() was called
    assert executor.last_dispatch_payload is None  # .dispatch() NOT called


def test_async_dispatch_no_executor_falls_through_to_sync():
    """When dispatcher returns no executor, async path is skipped, sync runs."""
    dispatcher = DummyWorkerDispatcher(executor=None)
    pending_store = MemoryPendingTaskStore()
    cc = ConcurrencyControl()

    uc = _make_uc(
        worker_dispatcher=dispatcher,
        pending_task_store=pending_store,
        concurrency_control=cc,
    )
    result = uc.execute(ExecuteTaskInput(
        task_id="t-noexec", run_id="r1", node_id="n1", worker_type="w1",
    ))

    # Falls through to sync path (no executor → placeholder result)
    assert result["status"] == "COMPLETED"
    assert pending_store.count() == 0
    assert cc.active_count == 0


# --- Dedup (Event Mesh at-least-once redelivery) ---

def test_execute_task_dedup_skips_duplicate_dispatch():
    """A redelivery of an in-flight task must NOT dispatch the worker again, and
    must release the slot it acquired (no concurrency leak) — the safety net for
    at-least-once delivery under concurrent consumers."""
    store = MemoryPendingTaskStore()
    executor = DummyExecutor()  # async path uses dispatch()
    dispatcher = DummyWorkerDispatcher(executor=executor)
    cc = ConcurrencyControl(max_concurrent=10)
    uc = _make_uc(
        worker_dispatcher=dispatcher,
        pending_task_store=store,
        concurrency_control=cc,
    )

    # First delivery → dispatched, stored, one slot held (awaiting callback).
    r1 = uc.execute(ExecuteTaskInput(task_id="dup-1", run_id="r1", node_id="n1", worker_type="w1"))
    assert r1["status"] == "DISPATCHED"
    assert store.count() == 1
    assert cc.active_count == 1
    assert executor.last_dispatch_payload is not None

    # Redelivery of the same task_id → DUPLICATE, no second dispatch, slot freed.
    executor.last_dispatch_payload = None
    r2 = uc.execute(ExecuteTaskInput(task_id="dup-1", run_id="r1", node_id="n1", worker_type="w1"))
    assert r2["status"] == "DUPLICATE"
    assert executor.last_dispatch_payload is None  # did NOT dispatch again
    assert store.count() == 1                      # original entry kept
    assert cc.active_count == 1                    # duplicate's slot released (no leak)
