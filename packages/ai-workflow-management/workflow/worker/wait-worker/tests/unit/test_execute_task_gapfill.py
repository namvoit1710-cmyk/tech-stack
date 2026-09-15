"""Gap-fill unit spec for the wait-worker ExecuteTaskUseCase.

Authored by unit-smith (unit-test-coverage sweep, 2026-07-02) to close the
4 uncovered lines the baseline run left in execute_task_usecase.py
(174, 237, 242-243) and to add the boundary case-types the contract mandates
(zero delay, max delay). Every assertion is grounded in the source, not theory.

- Line 174: timeout_seconds < 0 -> ValueError branch.
- Line 237: _to_int given a bool -> returns default (bool is-a-int guard).
- Lines 242-243: _to_int given a non-numeric string -> except -> default.

Sleep is patched at the module seam so the tests are instant (never sleep).
"""
from unittest.mock import patch

import pytest

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.value_objects.wait_status import WaitStatus
from app.layer1_domain.value_objects.wait_type import WaitType
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    ExecuteTaskCommand,
)

SLEEP_PATH = "app.layer2_application.features.execute_task.use_cases.execute_task_usecase.time.sleep"


# ---------------------------------------------------------------------------
# Invalid input / failure path — negative timeout (line 174)
# ---------------------------------------------------------------------------

class TestNegativeTimeout:
    def test_negative_timeout_errors(self, logger, monitor):
        """Line 174: timeout_seconds < 0 raises ValueError -> ERROR result."""
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="nt1",
            action="wait",
            inputs={
                "wait_type": WaitType.FIXED_DURATION.value,
                "duration_seconds": 10,
                "timeout_seconds": -1,
            },
        ))
        assert result.status == TaskStatus.ERROR
        assert result.outputs["wait_status"] == WaitStatus.ERROR.value
        assert "timeout_seconds must be >= 0" in result.outputs["message"]
        assert result.error is not None

    def test_negative_string_timeout_errors(self, logger, monitor):
        """Coerced negative timeout string also trips line 174."""
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="nt2",
            action="wait",
            inputs={
                "wait_type": WaitType.FIXED_DURATION.value,
                "duration_seconds": 10,
                "timeout_seconds": "-30",
            },
        ))
        assert result.status == TaskStatus.ERROR
        assert "timeout_seconds must be >= 0" in result.outputs["message"]


# ---------------------------------------------------------------------------
# _to_int coercion edges (lines 237, 242-243)
# ---------------------------------------------------------------------------

class TestToIntCoercion:
    @patch(SLEEP_PATH)
    def test_bool_duration_falls_back_to_default(self, mock_sleep, logger, monitor):
        """Line 237: bool is an int subclass -> guarded, returns default (60)."""
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="ti1",
            action="wait",
            inputs={
                "wait_type": WaitType.FIXED_DURATION.value,
                "duration_seconds": True,  # bool -> default, not 1
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        mock_sleep.assert_called_once_with(60)

    @patch(SLEEP_PATH)
    def test_nonnumeric_string_duration_falls_back_to_default(self, mock_sleep, logger, monitor):
        """Lines 242-243: non-numeric string -> ValueError caught -> default (60)."""
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="ti2",
            action="wait",
            inputs={
                "wait_type": WaitType.FIXED_DURATION.value,
                "duration_seconds": "abc",  # unparseable -> default
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        mock_sleep.assert_called_once_with(60)

    @patch(SLEEP_PATH)
    def test_list_duration_type_error_falls_back(self, mock_sleep, logger, monitor):
        """Lines 242-243: a non-coercible type (list) -> TypeError caught -> default."""
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="ti3",
            action="wait",
            inputs={
                "wait_type": WaitType.FIXED_DURATION.value,
                "duration_seconds": [1, 2, 3],  # TypeError on int() -> default
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        mock_sleep.assert_called_once_with(60)


# ---------------------------------------------------------------------------
# Boundary case-types the contract calls out (zero delay, max delay)
# ---------------------------------------------------------------------------

class TestBoundaryDurations:
    @patch(SLEEP_PATH)
    def test_zero_duration_completes(self, mock_sleep, logger, monitor):
        """Boundary: zero delay is valid (>= 0), sleeps 0, completes."""
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="bd1",
            action="wait",
            inputs={
                "wait_type": WaitType.FIXED_DURATION.value,
                "duration_seconds": 0,
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["wait_status"] == WaitStatus.COMPLETED.value
        assert result.outputs["actual_duration_seconds"] >= 0
        mock_sleep.assert_called_once_with(0)

    @patch(SLEEP_PATH)
    def test_max_duration_within_max_timeout_completes(self, mock_sleep, logger, monitor):
        """Boundary: schema max duration (86400s) under the 7-day timeout completes."""
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="bd2",
            action="wait",
            inputs={
                "wait_type": WaitType.FIXED_DURATION.value,
                "duration_seconds": 86400,
                "timeout_seconds": 604800,
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["wait_status"] == WaitStatus.COMPLETED.value
        mock_sleep.assert_called_once_with(86400)

    @patch(SLEEP_PATH)
    def test_zero_timeout_disables_timeout_check(self, mock_sleep, logger, monitor):
        """Boundary: timeout_seconds == 0 disables the `> timeout` guard (source: `if timeout_seconds > 0`)."""
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="bd3",
            action="wait",
            inputs={
                "wait_type": WaitType.FIXED_DURATION.value,
                "duration_seconds": 100000,  # huge, but timeout=0 means no cap
                "timeout_seconds": 0,
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["wait_status"] == WaitStatus.COMPLETED.value
        mock_sleep.assert_called_once_with(100000)


# ---------------------------------------------------------------------------
# until_datetime boundary/failure edges
# ---------------------------------------------------------------------------

class TestUntilDatetimeMoreEdges:
    def test_malformed_datetime_string_errors(self, logger, monitor):
        """Failure path: unparseable ISO string -> fromisoformat ValueError -> ERROR."""
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="ud1",
            action="wait",
            inputs={
                "wait_type": WaitType.UNTIL_DATETIME.value,
                "until_datetime": "not-a-date",
            },
        ))
        assert result.status == TaskStatus.ERROR
        assert result.outputs["wait_status"] == WaitStatus.ERROR.value

    @patch(SLEEP_PATH)
    def test_zero_timeout_disables_until_datetime_timeout(self, mock_sleep, logger, monitor):
        """Boundary: timeout_seconds == 0 also disables the until_datetime cap."""
        from datetime import datetime, timedelta, timezone
        far = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="ud2",
            action="wait",
            inputs={
                "wait_type": WaitType.UNTIL_DATETIME.value,
                "until_datetime": far,
                "timeout_seconds": 0,
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["wait_status"] == WaitStatus.COMPLETED.value
        mock_sleep.assert_called_once()


# ---------------------------------------------------------------------------
# Monitor evidence — happy path emits the expected metrics
# ---------------------------------------------------------------------------

class TestMonitorMetrics:
    @patch(SLEEP_PATH)
    def test_success_tracks_completed_metric(self, mock_sleep, logger, monitor):
        """Happy path: monitor records request + completed metrics."""
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        uc.execute(ExecuteTaskCommand(
            task_id="mm1",
            action="wait",
            inputs={"wait_type": WaitType.FIXED_DURATION.value, "duration_seconds": 5},
        ))
        metric_names = [m for m, _ in monitor.tracked]
        assert "wait_task_requests" in metric_names
        assert f"wait_task_{WaitStatus.COMPLETED.value}" in metric_names

    def test_error_tracks_error_metric(self, logger, monitor):
        """Failure path: monitor records the error metric."""
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        uc.execute(ExecuteTaskCommand(
            task_id="mm2",
            action="wait",
            inputs={"wait_type": "bogus"},
        ))
        metric_names = [m for m, _ in monitor.tracked]
        assert "wait_task_error" in metric_names
