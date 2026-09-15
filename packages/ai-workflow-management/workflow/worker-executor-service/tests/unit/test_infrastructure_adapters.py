import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer4_frameworks.logger.app_logger import StandardLogger, _safe_extra
from app.layer4_frameworks.messaging.console_result_publisher import ConsoleResultPublisher
from app.layer4_frameworks.messaging.console_task_queue import ConsoleTaskQueue
from app.layer4_frameworks.providers.mock_decision_switch_client import MockDecisionSwitchClient
from app.layer4_frameworks.monitoring.prometheus import PrometheusMonitor


# --- StandardLogger ---

def test_safe_extra_returns_none_for_empty():
    assert _safe_extra({}) is None


def test_safe_extra_prefixes_keys():
    result = _safe_extra({"task_id": "t1", "worker": "w1"})
    assert result == {"ctx_task_id": "t1", "ctx_worker": "w1"}


def test_standard_logger_info():
    logger = StandardLogger()
    logger.info("test message", task_id="t1")


def test_standard_logger_error():
    logger = StandardLogger()
    logger.error("error message", code="500")


def test_standard_logger_warning():
    logger = StandardLogger()
    logger.warning("warn message")


def test_standard_logger_debug():
    logger = StandardLogger()
    logger.debug("debug message", detail="x")


# --- ConsoleResultPublisher ---

def test_console_result_publisher(capsys):
    pub = ConsoleResultPublisher()
    pub.publish_result({"task_id": "t1"})
    captured = capsys.readouterr()
    assert "Publishing" in captured.out


# --- ConsoleTaskQueue ---

def test_console_task_queue_consume(capsys):
    queue = ConsoleTaskQueue()
    queue.consume(lambda x: x)
    captured = capsys.readouterr()
    assert "Consumer started" in captured.out


def test_console_task_queue_acknowledge(capsys):
    queue = ConsoleTaskQueue()
    queue.acknowledge({"id": "m1"})
    captured = capsys.readouterr()
    assert "Acknowledged" in captured.out


# --- MockDecisionSwitchClient ---

def test_mock_decision_switch_dispatch(capsys):
    client = MockDecisionSwitchClient()
    result = client.dispatch_task({"task_id": "t1"})
    assert result["status"] == "dispatched"
    assert result["worker_id"] == "mock-worker-001"


# --- PrometheusMonitor ---

def test_prometheus_track():
    monitor = PrometheusMonitor()
    # track should not raise — it records to Prometheus histogram/gauge
    monitor.track("duration", 42.5)
    monitor.track("active", 3)


def test_prometheus_increment():
    monitor = PrometheusMonitor()
    # SA-2034: increment is now a real wes_events_total{event} counter (value-based).
    monitor.increment("requests")
    monitor.increment("requests", 4)
    assert 'wes_events_total{event="requests"}' in monitor.metrics_output()[0].decode()


def test_prometheus_typed_methods():
    monitor = PrometheusMonitor()
    monitor.task_received("http-request")
    monitor.task_completed("http-request", 1.5)
    monitor.task_failed("http-request", "WORKER_ERROR")
    monitor.task_dispatched_async("http-request")
    monitor.callback_received("success")
    monitor.worker_dispatch_duration("http-request", 0.25)
    monitor.concurrency_rejected()
    monitor.set_active_tasks(5)
    monitor.set_pending_tasks(2)
    monitor.set_registered_workers(3)


def test_prometheus_metrics_output():
    monitor = PrometheusMonitor()
    body, content_type = monitor.metrics_output()
    assert isinstance(body, bytes)
    assert "text/plain" in content_type or "text/plain" in str(content_type)
