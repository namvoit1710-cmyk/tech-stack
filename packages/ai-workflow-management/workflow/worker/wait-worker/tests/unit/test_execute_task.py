import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.value_objects.wait_status import WaitStatus
from app.layer1_domain.value_objects.wait_type import WaitType
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    ExecuteTaskCommand,
)


class TestFixedDurationWait:
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.time.sleep")
    def test_fixed_duration_success(self, mock_sleep, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t1",
            action="wait",
            inputs={
                "wait_type": WaitType.FIXED_DURATION.value,
                "duration_seconds": 30,
                "duration_minutes": 0,
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["wait_status"] == WaitStatus.COMPLETED.value
        assert result.outputs["wait_type"] == WaitType.FIXED_DURATION.value
        mock_sleep.assert_called_once_with(30)

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.time.sleep")
    def test_fixed_duration_larger_value(self, mock_sleep, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t2",
            action="wait",
            inputs={
                "wait_type": WaitType.FIXED_DURATION.value,
                "duration_seconds": 150,
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        assert "150 seconds" in result.outputs["message"]
        mock_sleep.assert_called_once_with(150)

    def test_fixed_duration_exceeds_timeout(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t3",
            action="wait",
            inputs={
                "wait_type": WaitType.FIXED_DURATION.value,
                "duration_seconds": 7200,
                "timeout_seconds": 3600,
            },
        ))
        assert result.status == TaskStatus.ERROR
        assert result.outputs["wait_status"] == WaitStatus.TIMED_OUT.value


class TestUntilDatetimeWait:
    def test_past_datetime_completes_immediately(self, logger, monitor):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t4",
            action="wait",
            inputs={
                "wait_type": WaitType.UNTIL_DATETIME.value,
                "until_datetime": past,
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        assert "past" in result.outputs["message"].lower()

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.time.sleep")
    def test_future_datetime_schedules(self, mock_sleep, logger, monitor):
        future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t5",
            action="wait",
            inputs={
                "wait_type": WaitType.UNTIL_DATETIME.value,
                "until_datetime": future,
                "timeout_seconds": 86400,
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["wait_status"] == WaitStatus.COMPLETED.value
        mock_sleep.assert_called_once()
        slept = mock_sleep.call_args[0][0]
        assert 550 < slept < 650  # ~600s (10 minutes)

    def test_missing_until_datetime_errors(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t6",
            action="wait",
            inputs={"wait_type": WaitType.UNTIL_DATETIME.value},
        ))
        assert result.status == TaskStatus.ERROR
        assert result.outputs["wait_status"] == WaitStatus.ERROR.value


class TestManualApprovalWait:
    def test_manual_approval_registers(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t7",
            action="wait",
            inputs={
                "wait_type": WaitType.MANUAL_APPROVAL.value,
                "reason": "Manager sign-off required",
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["wait_status"] == WaitStatus.COMPLETED.value
        assert "Manager sign-off" in result.outputs["message"]


class TestUntilDatetimeEdgeCases:
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.time.sleep")
    def test_naive_datetime_treated_as_utc(self, mock_sleep, logger, monitor):
        """Line 253: naive datetime (no tzinfo) gets UTC assigned."""
        future_naive = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t9",
            action="wait",
            inputs={
                "wait_type": WaitType.UNTIL_DATETIME.value,
                "until_datetime": future_naive,
                "timeout_seconds": 86400,
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["wait_status"] == WaitStatus.COMPLETED.value
        mock_sleep.assert_called_once()
        slept = mock_sleep.call_args[0][0]
        assert 250 < slept < 350  # ~300s (5 minutes)

    def test_future_datetime_exceeds_timeout(self, logger, monitor):
        """Line 264: until_datetime that exceeds timeout returns TIMED_OUT."""
        far_future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t10",
            action="wait",
            inputs={
                "wait_type": WaitType.UNTIL_DATETIME.value,
                "until_datetime": far_future,
                "timeout_seconds": 60,
            },
        ))
        assert result.status == TaskStatus.ERROR
        assert result.outputs["wait_status"] == WaitStatus.TIMED_OUT.value
        assert "exceeds timeout" in result.outputs["message"]


class TestUnknownWaitType:
    def test_unknown_type_errors(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t8",
            action="wait",
            inputs={"wait_type": "invalid_type"},
        ))
        assert result.status == TaskStatus.ERROR
        assert result.outputs["wait_status"] == WaitStatus.ERROR.value


class TestStringNumericInputs:
    """Numeric inputs arrive as strings from the form / variable binding.

    Regression for: "Node 'wait' failed: '<' not supported between instances
    of 'str' and 'int'" — the duration/timeout comparisons crashed when the
    values were strings instead of ints.
    """

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.time.sleep")
    def test_string_duration_is_coerced(self, mock_sleep, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="s1",
            action="wait",
            inputs={
                "wait_type": WaitType.FIXED_DURATION.value,
                "duration_seconds": "30",
                "timeout_seconds": "3600",
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["wait_status"] == WaitStatus.COMPLETED.value
        mock_sleep.assert_called_once_with(30)

    def test_string_duration_exceeds_string_timeout(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="s2",
            action="wait",
            inputs={
                "wait_type": WaitType.FIXED_DURATION.value,
                "duration_seconds": "7200",
                "timeout_seconds": "3600",
            },
        ))
        assert result.status == TaskStatus.ERROR
        assert result.outputs["wait_status"] == WaitStatus.TIMED_OUT.value

    def test_negative_string_duration_errors(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="s3",
            action="wait",
            inputs={
                "wait_type": WaitType.FIXED_DURATION.value,
                "duration_seconds": "-5",
            },
        ))
        assert result.status == TaskStatus.ERROR
        assert result.outputs["wait_status"] == WaitStatus.ERROR.value
        assert "duration_seconds must be >= 0" in result.outputs["message"]

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.time.sleep")
    def test_blank_and_missing_fall_back_to_defaults(self, mock_sleep, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="s4",
            action="wait",
            inputs={
                "wait_type": WaitType.FIXED_DURATION.value,
                "duration_seconds": "",
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        mock_sleep.assert_called_once_with(60)  # blank -> default 60
