import time
from datetime import datetime, timezone
from typing import Any

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.interfaces.logger_interface import ILogger
from worker_sdk.layer2_application.interfaces.monitor_interface import IMonitor
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
    ExecuteTaskResult,
)
from worker_sdk.layer1_domain.auto_form_mapper import (
    AutoFormField, FieldConfig, ValidationRule, ConditionConfig,
    OutputType, FieldControl, FieldWrapper, ConditionOperator,
    text_field, number_field, select_field,
)
from app.layer1_domain.value_objects.wait_type import WaitType
from app.layer1_domain.value_objects.wait_status import WaitStatus


# -- Input Schema ------------------------------------------------------

_INPUT_FIELDS: list[AutoFormField] = [
    select_field(
        "wait_type",
        "Wait Type",
        options=[
            (WaitType.FIXED_DURATION.value, "Fixed Duration"),
            (WaitType.UNTIL_DATETIME.value, "Until Datetime"),
            (WaitType.MANUAL_APPROVAL.value, "Manual Approval"),
        ],
        placeholder="Select wait type",
        rules=[ValidationRule(method="required")],
    ),
    AutoFormField(
        key="duration_seconds",
        output_type=OutputType.NUMBER,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Duration (seconds)"},
            control_props={"type": "number", "placeholder": "60", "value": 60},
            rules=[
                ValidationRule(method="min", value=0, message="Minimum is 0 seconds"),
                ValidationRule(method="max", value=86400, message="Maximum is 86400 seconds (24 hours)"),
            ],
            condition=ConditionConfig(
                field_key="wait_type",
                operator=ConditionOperator.NOT_EQUALS,
                condition_value=WaitType.FIXED_DURATION.value,
            ),
        ),
    ),
    AutoFormField(
        key="until_datetime",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Wait Until (ISO 8601)"},
            control_props={"placeholder": "2025-06-01T12:00:00Z"},
            condition=ConditionConfig(
                field_key="wait_type",
                operator=ConditionOperator.NOT_EQUALS,
                condition_value=WaitType.UNTIL_DATETIME.value,
            ),
        ),
    ),
    number_field(
        "timeout_seconds",
        "Timeout (seconds)",
        placeholder="3600",
        default=3600,
        rules=[
            ValidationRule(method="min", value=0, message="Minimum is 0 seconds"),
            ValidationRule(method="max", value=604800, message="Maximum is 604800 seconds (7 days)"),
        ],
    ),
    text_field(
        "reason",
        "Reason",
        placeholder="Waiting for approval",
        rules=[
            ValidationRule(method="max", value=500, message="Reason must be 500 characters or fewer"),
        ],
    ),
]

WAIT_INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in _INPUT_FIELDS]


# -- Output Schema -----------------------------------------------------

_OUTPUT_FIELDS: list[AutoFormField] = [
    select_field(
        "wait_status",
        "Wait Status",
        options=[
            (WaitStatus.COMPLETED.value, "Completed"),
            (WaitStatus.TIMED_OUT.value, "Timed Out"),
            (WaitStatus.CANCELLED.value, "Cancelled"),
            (WaitStatus.ERROR.value, "Error"),
        ],
        rules=[ValidationRule(method="required")],
    ),
    select_field(
        "wait_type",
        "Wait Type Used",
        options=[
            (WaitType.FIXED_DURATION.value, "Fixed Duration"),
            (WaitType.UNTIL_DATETIME.value, "Until Datetime"),
            (WaitType.MANUAL_APPROVAL.value, "Manual Approval"),
        ],
        rules=[ValidationRule(method="required")],
    ),
    text_field(
        "started_at",
        "Started At",
        rules=[ValidationRule(method="required")],
    ),
    text_field(
        "completed_at",
        "Completed At",
        rules=[ValidationRule(method="required")],
    ),
    number_field(
        "actual_duration_seconds",
        "Actual Duration (seconds)",
        rules=[ValidationRule(method="required")],
    ),
    text_field("reason", "Reason"),
    text_field("message", "Message"),
]

WAIT_OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in _OUTPUT_FIELDS]


# -- Use Case ----------------------------------------------------------

class ExecuteTaskUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.monitor = monitor

    def execute(self, request: ExecuteTaskCommand) -> ExecuteTaskResult:
        self.logger.info(
            "Executing wait task",
            task_id=request.task_id,
            action=request.action,
        )
        self.monitor.track("wait_task_requests", 1)

        start = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()

        try:
            inputs = request.inputs
            wait_type = inputs.get("wait_type", WaitType.FIXED_DURATION.value)
            reason = inputs.get("reason", "")
            timeout_seconds = self._to_int(inputs.get("timeout_seconds", 3600), 3600)

            # -- Validate duration is non-negative --
            duration_seconds = self._to_int(inputs.get("duration_seconds", 60), 60)
            if duration_seconds < 0:
                raise ValueError(f"duration_seconds must be >= 0, got {duration_seconds}")

            # -- Validate timeout is non-negative --
            if timeout_seconds < 0:
                raise ValueError(f"timeout_seconds must be >= 0, got {timeout_seconds}")

            if wait_type == WaitType.FIXED_DURATION.value:
                result = self._handle_fixed_duration(inputs, timeout_seconds)
            elif wait_type == WaitType.UNTIL_DATETIME.value:
                result = self._handle_until_datetime(inputs, timeout_seconds)
            elif wait_type == WaitType.MANUAL_APPROVAL.value:
                result = self._handle_manual_approval(inputs, timeout_seconds)
            else:
                raise ValueError(f"Unknown wait_type: {wait_type}")

            duration_ms = (time.monotonic() - start) * 1000
            completed_at = datetime.now(timezone.utc).isoformat()

            wait_status = result.get("wait_status", WaitStatus.COMPLETED.value)
            task_status = TaskStatus.SUCCESS if wait_status == WaitStatus.COMPLETED.value else TaskStatus.ERROR

            self.monitor.track("wait_task_duration_ms", duration_ms)
            self.monitor.track(f"wait_task_{wait_status}", 1)

            outputs = {
                "wait_status": wait_status,
                "wait_type": wait_type,
                "started_at": started_at,
                "completed_at": completed_at,
                "actual_duration_seconds": duration_ms / 1000,
                "reason": reason,
                "message": result.get("message", ""),
            }

            return ExecuteTaskResult(
                task_id=request.task_id,
                status=task_status,
                outputs=outputs,
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = (time.monotonic() - start) * 1000
            completed_at = datetime.now(timezone.utc).isoformat()
            self.logger.error("Wait task failed", task_id=request.task_id, error=str(e))
            self.monitor.track("wait_task_error", 1)

            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.ERROR,
                outputs={
                    "wait_status": WaitStatus.ERROR.value,
                    "wait_type": request.inputs.get("wait_type", "unknown"),
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "actual_duration_seconds": duration_ms / 1000,
                    "reason": request.inputs.get("reason", ""),
                    "message": str(e),
                },
                error=str(e),
                duration_ms=duration_ms,
            )

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        """Coerce an input (may arrive as a string from the form/binding) to int, default on failure."""
        if isinstance(value, bool):
            return default
        if value is None or value == "":
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default


    def _handle_fixed_duration(self, inputs: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
        """Wait for a fixed number of seconds."""
        total_seconds = self._to_int(inputs.get("duration_seconds", 60), 60)

        if timeout_seconds > 0 and total_seconds > timeout_seconds:
            return {
                "wait_status": WaitStatus.TIMED_OUT.value,
                "message": f"Requested duration ({total_seconds}s) exceeds timeout ({timeout_seconds}s).",
            }

        self.logger.info(f"Fixed duration wait: {total_seconds}s")
        time.sleep(total_seconds)
        return {
            "wait_status": WaitStatus.COMPLETED.value,
            "message": f"Wait completed after {total_seconds} seconds.",
        }

    def _handle_until_datetime(self, inputs: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
        """Validate the target datetime and compute the wait duration."""
        until_str = inputs.get("until_datetime")
        if not until_str:
            raise ValueError("until_datetime is required when wait_type is 'until_datetime'.")

        target_dt = datetime.fromisoformat(until_str)
        now = datetime.now(timezone.utc)

        if target_dt.tzinfo is None:
            target_dt = target_dt.replace(tzinfo=timezone.utc)

        delta_seconds = (target_dt - now).total_seconds()

        if delta_seconds <= 0:
            return {
                "wait_status": WaitStatus.COMPLETED.value,
                "message": f"Target datetime {until_str} is in the past. Proceeding immediately.",
            }

        if timeout_seconds > 0 and delta_seconds > timeout_seconds:
            return {
                "wait_status": WaitStatus.TIMED_OUT.value,
                "message": f"Wait until {until_str} ({delta_seconds:.0f}s away) exceeds timeout ({timeout_seconds}s).",
            }

        self.logger.info(f"Until-datetime wait: target={until_str}, delta={delta_seconds:.0f}s")
        time.sleep(delta_seconds)
        return {
            "wait_status": WaitStatus.COMPLETED.value,
            "message": f"Wait completed until {until_str} ({delta_seconds:.0f}s elapsed).",
        }

    def _handle_manual_approval(self, inputs: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
        """Register a manual approval wait. Actual approval is external."""
        reason = inputs.get("reason", "Manual approval required")
        self.logger.info(f"Manual approval wait: reason={reason}")
        # NOTE: The workflow orchestrator will pause execution until approval is received.
        # This worker registers the wait and returns the pending state.
        return {
            "wait_status": WaitStatus.COMPLETED.value,
            "message": f"Manual approval wait registered. Reason: {reason}",
        }
