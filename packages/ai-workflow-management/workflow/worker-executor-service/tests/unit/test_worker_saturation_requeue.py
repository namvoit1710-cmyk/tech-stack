"""SA-1531 — worker 429/503 acceptance = BACKPRESSURE (REQUEUE), not failure.

Design (sealed 2026-07-02): a saturated worker (SA-1530's 429 gate, or a 503)
must NOT produce task.failed to WCP and must NOT consume the retry budget.
The use case returns error.code=WORKER_SATURATED; the consume callback raises
on that code (same rail as CONCURRENCY_LIMIT) so the broker skips the ack and
Event Mesh redelivers after the lock TTL. Also: per-type pending-timeout
overrides via TASK_TIMEOUT_OVERRIDES.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from datetime import datetime

from app.layer1_domain.entities.execution_task import ExecutionTask
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskInput,
    ExecuteTaskUseCase,
    _saturation_status,
)


def _http_error(status: int) -> httpx.HTTPStatusError:
    resp = MagicMock()
    resp.status_code = status
    return httpx.HTTPStatusError(str(status), request=MagicMock(), response=resp)


class _Config:
    CALLBACK_BASE_URL = "http://executor:8004"
    SERVICE_PORT = 8004
    TASK_TIMEOUT_SECONDS = 120
    TASK_TIMEOUT_OVERRIDES = ""
    TASK_CALLBACK_GRACE_SECONDS = 30
    TASK_TIMEOUT_MAX_SECONDS = 3600
    MAX_CONCURRENT_TASKS = 10


def _task(worker_type: str, payload=None, node_config=None) -> ExecutionTask:
    return ExecutionTask(
        id="t1", run_id="r1", node_id="n1", worker_type=worker_type,
        payload=payload or {}, node_config=node_config or {},
    )


def _make_usecase(dispatch_exc=None, config=None):
    executor = MagicMock()
    if dispatch_exc is not None:
        executor.dispatch.side_effect = dispatch_exc
    dispatcher = MagicMock()
    dispatcher.get_executor.return_value = executor

    pending = MagicMock()
    pending.store_if_absent.return_value = True

    reporter = MagicMock()
    uc = ExecuteTaskUseCase(
        logger=MagicMock(),
        app_config=config or _Config(),
        task_queue=MagicMock(),
        result_publisher=MagicMock(),
        decision_switch=MagicMock(),
        worker_dispatcher=dispatcher,
        result_reporter=reporter,
        pending_task_store=pending,
        monitor=MagicMock(),
    )
    return uc, executor, pending, reporter


def _input(worker_type="http-request-worker"):
    return ExecuteTaskInput(
        task_id="t1", run_id="r1", node_id="n1",
        worker_type=worker_type, payload={"a": 1}, node_config={},
    )


class TestSaturationDetector:
    def test_429_and_503_detected(self):
        assert _saturation_status(_http_error(429)) == 429
        assert _saturation_status(_http_error(503)) == 503

    def test_other_errors_not_saturation(self):
        assert _saturation_status(_http_error(500)) is None
        assert _saturation_status(RuntimeError("boom")) is None


class TestWorker429Requeue:
    def test_429_returns_requeue_no_failure_report(self):
        uc, executor, pending, reporter = _make_usecase(dispatch_exc=_http_error(429))
        result = uc.execute(_input())
        assert result["status"] == "REQUEUE"
        assert result["error"]["code"] == "WORKER_SATURATED"
        assert result["error"]["retryable"] is True
        # NOT a failure: nothing reported to WCP (no task.failed noise).
        reporter.report_failed.assert_not_called()
        # Slot + pending fully released for the redelivery.
        pending.remove.assert_called_once_with("t1")
        assert uc._concurrency.active_count == 0

    def test_503_same_requeue_rail(self):
        uc, _, _, reporter = _make_usecase(dispatch_exc=_http_error(503))
        result = uc.execute(_input())
        assert result["error"]["code"] == "WORKER_SATURATED"
        reporter.report_failed.assert_not_called()

    def test_500_stays_on_legacy_failure_path(self):
        uc, _, pending, reporter = _make_usecase(dispatch_exc=_http_error(500))
        result = uc.execute(_input())
        assert result["error"]["code"] == "WORKER_ERROR"  # legacy handler
        reporter.report_failed.assert_called_once()  # real failures still report

    def test_attempt_budget_untouched_by_saturation(self):
        """Backpressure must not consume the retry budget: the REQUEUE path
        never calls RetryPolicy.evaluate (spy on it)."""
        uc, _, _, _ = _make_usecase(dispatch_exc=_http_error(429))
        uc._retry_policy = MagicMock()
        uc.execute(_input())
        uc._retry_policy.evaluate.assert_not_called()


class TestPerTypeTimeoutOverrides:
    """SA-1531 overrides are keyed by WORKER_TYPE, never by the CF app name.

    The first version of this test asserted ``_task_timeout_for("agent-worker")``
    == 600. That is green and wrong: no worker registers ``agent-worker`` as its
    worker_type — the agent worker's WORKER_TYPE is ``agent``, the code worker's
    is ``code``, the http worker's is ``http_request``. The dev deployment shipped
    a map keyed by app name, so it matched nothing and every type silently ran on
    the 120s default, including the agent worker the flag was written for. These
    assertions use the strings that actually arrive on a task.
    """

    def test_override_hit_on_real_worker_type(self):
        cfg = _Config()
        cfg.TASK_TIMEOUT_OVERRIDES = json.dumps({"agent": 600, "code": 300})
        uc, _, _, _ = _make_usecase(config=cfg)
        assert uc._task_timeout_for(_task("agent")) == 600
        assert uc._task_timeout_for(_task("code")) == 300
        assert uc._task_timeout_for(_task("http_request")) == 120  # absent -> default

    def test_cf_app_name_as_key_matches_nothing(self):
        """Regression guard for the shipped bug: an app-name key must not appear
        to work. If someone re-writes the map that way, this test fails."""
        cfg = _Config()
        cfg.TASK_TIMEOUT_OVERRIDES = json.dumps({"agent-worker": 600, "code-worker": 300})
        uc, _, _, _ = _make_usecase(config=cfg)
        assert uc._task_timeout_for(_task("agent")) == 120
        assert uc._task_timeout_for(_task("code")) == 120

    def test_empty_and_garbage_fall_back(self):
        cfg = _Config()
        uc, _, _, _ = _make_usecase(config=cfg)
        assert uc._task_timeout_for(_task("x")) == 120
        cfg.TASK_TIMEOUT_OVERRIDES = "{not json"
        assert uc._task_timeout_for(_task("x")) == 120


class TestNodeDeclaredDeadline:
    """The executor must never give up before the worker's OWN deadline.

    An http node configured with timeout_seconds=300 used to be swept at the 120s
    default and reported as "Worker http_request did not callback in time" while
    the worker was still waiting on a healthy upstream — a false failure on a task
    that was about to succeed.
    """

    def test_declared_deadline_above_the_default_raises_it(self):
        uc, _, _, _ = _make_usecase()
        task = _task("http_request", payload={"timeout_seconds": 300})
        assert uc._task_timeout_for(task) == 330  # 300 + 30s callback grace

    def test_declared_deadline_below_the_default_does_not_lower_it(self):
        """Config is a floor, not a target — a 30s node keeps the 120s floor."""
        uc, _, _, _ = _make_usecase()
        task = _task("http_request", payload={"timeout_seconds": 30})
        assert uc._task_timeout_for(task) == 120

    def test_form_supplied_string_is_accepted(self):
        uc, _, _, _ = _make_usecase()
        task = _task("http_request", payload={"timeout_seconds": "300"})
        assert uc._task_timeout_for(task) == 330

    @pytest.mark.parametrize(
        "value", ["${node1.timeout}", "", None, 0, -5, True, [300], {"a": 1}]
    )
    def test_unusable_values_are_ignored(self, value):
        """An unresolved template, a blank, a bool, or a non-positive number must
        fall back to config rather than raise or produce a nonsense deadline."""
        uc, _, _, _ = _make_usecase()
        task = _task("http_request", payload={"timeout_seconds": value})
        assert uc._task_timeout_for(task) == 120

    def test_declared_deadline_is_clamped_to_the_ceiling(self):
        """Caller-supplied data: a huge value must not pin a concurrency slot."""
        uc, _, _, _ = _make_usecase()
        task = _task("http_request", payload={"timeout_seconds": 999999})
        assert uc._task_timeout_for(task) == 3600  # TASK_TIMEOUT_MAX_SECONDS

    def test_node_config_is_consulted_when_the_payload_is_silent(self):
        uc, _, _, _ = _make_usecase()
        task = _task("http_request", node_config={"timeout_seconds": 300})
        assert uc._task_timeout_for(task) == 330

    def test_larger_of_override_and_declared_deadline_wins(self):
        cfg = _Config()
        cfg.TASK_TIMEOUT_OVERRIDES = json.dumps({"http_request": 900})
        uc, _, _, _ = _make_usecase(config=cfg)
        task = _task("http_request", payload={"timeout_seconds": 300})
        assert uc._task_timeout_for(task) == 900  # override floor beats 300+30

    def test_config_without_the_new_knobs_still_works(self):
        """An older injected config lacking the grace/ceiling attributes must not
        crash — both are read through getattr with a default."""

        class _Old:
            CALLBACK_BASE_URL = "http://executor:8004"
            SERVICE_PORT = 8004
            TASK_TIMEOUT_SECONDS = 120
            TASK_TIMEOUT_OVERRIDES = ""
            MAX_CONCURRENT_TASKS = 10

        uc, _, _, _ = _make_usecase(config=_Old())
        task = _task("http_request", payload={"timeout_seconds": 300})
        assert uc._task_timeout_for(task) == 330

    def test_dispatch_stores_the_resolved_deadline(self):
        """End-to-end through execute(): the pending entry the sweeper reads must
        carry the raised deadline, not the 120s default."""
        uc, _, pending, _ = _make_usecase()
        uc.execute(ExecuteTaskInput(
            task_id="t1", run_id="r1", node_id="n1",
            worker_type="http_request",
            payload={"timeout_seconds": 300}, node_config={},
        ))
        entry = pending.store_if_absent.call_args[0][0]
        window = (
            datetime.fromisoformat(entry.timeout_at)
            - datetime.fromisoformat(entry.dispatched_at)
        ).total_seconds()
        assert 329 <= window <= 331
