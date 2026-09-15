import time
from typing import Any, Optional

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.interfaces.logger_interface import ILogger
from worker_sdk.layer2_application.interfaces.monitor_interface import IMonitor
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
    ExecuteTaskResult,
)
from worker_sdk.layer1_domain.auto_form_mapper import (
    AutoFormField,
    FieldConfig,
    FieldControl,
    FieldWrapper,
    OutputType,
    ValidationRule,
    number_field,
    select_field,
    textarea_field,
)

from app.layer1_domain.value_objects.code_language import CodeLanguage
from app.layer1_domain.value_objects.execution_mode import ExecutionMode
from app.layer2_application.interfaces.code_executor_interface import ICodeExecutor

# Config keys that drive the node itself rather than being user data. They are
# stripped from the data passed into the script so the script only sees the
# upstream payload, not its own configuration.
_CONFIG_KEYS = frozenset({"language", "code", "mode", "timeout_seconds"})

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 120


# -- Input Schema -------------------------------------------------------------

_INPUT_FIELDS: list[AutoFormField] = [
    select_field(
        key="language",
        label="Language",
        options=[
            (CodeLanguage.JAVASCRIPT.value, "JavaScript"),
            (CodeLanguage.PYTHON.value, "Python"),
        ],
        placeholder="Select language",
        rules=[ValidationRule(method="required")],
    ),
    select_field(
        key="mode",
        label="Run Mode",
        options=[
            (ExecutionMode.ALL_ITEMS.value, "Run Once for All Items"),
            (ExecutionMode.PER_ITEM.value, "Run Once per Item"),
        ],
        placeholder="Select run mode",
    ),
    # code editor — uses the auto-form CodeControl. The editor language hint
    # mirrors the selected `language` field so syntax highlighting matches.
    AutoFormField(
        key="code",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.CODE_CONTROL,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Code", "required": True},
            control_props={
                "language": "javascript",
                # Editor highlighting follows the sibling `language` select so
                # switching to Python re-tints the code editor live.
                "languageField": "language",
                "placeholder": (
                    "// Available: items (array), input (object)\n"
                    "// Return the data for downstream nodes.\n"
                    "return items;"
                ),
            },
            rules=[ValidationRule(method="required")],
        ),
    ),
    number_field(
        key="timeout_seconds",
        label="Timeout (seconds)",
        placeholder=str(DEFAULT_TIMEOUT_SECONDS),
        default=DEFAULT_TIMEOUT_SECONDS,
        rules=[
            ValidationRule(method="min", value=1, message="Minimum timeout is 1 second"),
            ValidationRule(
                method="max",
                value=MAX_TIMEOUT_SECONDS,
                message=f"Maximum timeout is {MAX_TIMEOUT_SECONDS} seconds",
            ),
        ],
    ),
]

CODE_INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in _INPUT_FIELDS]


# -- Output Schema ------------------------------------------------------------

_OUTPUT_FIELDS: list[AutoFormField] = [
    AutoFormField(
        key="result",
        output_type=OutputType.OBJECT,
        field_config=FieldConfig(
            field_control=FieldControl.CODE_CONTROL,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Result"},
            control_props={"language": "json", "readonly": True},
        ),
    ),
    textarea_field(
        key="logs",
        label="Logs",
        readonly=True,
    ),
    number_field(
        key="execution_time_ms",
        label="Execution Time (ms)",
        readonly=True,
    ),
]

CODE_OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in _OUTPUT_FIELDS]


# -- Use Case -----------------------------------------------------------------

class ExecuteTaskUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        code_executor: Optional[ICodeExecutor] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.monitor = monitor
        self.code_executor = code_executor

    def execute(self, request: ExecuteTaskCommand) -> ExecuteTaskResult:
        self.logger.info(
            "Executing code task",
            task_id=request.task_id,
            action=request.action,
        )
        self.monitor.track("code_task_requests", 1)

        start = time.monotonic()

        try:
            if self.code_executor is None:
                raise RuntimeError("code_executor not configured")

            # Inputs (runtime data + upstream payload) override static node
            # parameters, mirroring the other workers.
            inputs = {**request.parameters, **request.inputs}

            language = (inputs.get("language") or "").strip().lower()
            if language not in {e.value for e in CodeLanguage}:
                raise ValueError(
                    f"Invalid language: '{language}'. Must be one of: "
                    f"{', '.join(e.value for e in CodeLanguage)}"
                )

            code = inputs.get("code")
            if not code or not str(code).strip():
                raise ValueError("'code' is required.")

            mode = (inputs.get("mode") or ExecutionMode.ALL_ITEMS.value).strip().lower()
            if mode not in {e.value for e in ExecutionMode}:
                mode = ExecutionMode.ALL_ITEMS.value

            timeout_seconds = self._to_int(
                inputs.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
                default=DEFAULT_TIMEOUT_SECONDS,
            )
            if not (1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS):
                raise ValueError(
                    f"timeout_seconds must be between 1 and {MAX_TIMEOUT_SECONDS}, "
                    f"got {timeout_seconds}"
                )

            # The script sees the upstream payload (everything except the node's
            # own config) as `input`, and a normalized `items` list.
            input_data = {k: v for k, v in inputs.items() if k not in _CONFIG_KEYS}
            items = self._normalize_items(input_data)

            exec_result = self.code_executor.run(
                language=language,
                code=str(code),
                items=items,
                input_data=input_data,
                mode=mode,
                timeout_seconds=timeout_seconds,
            )

            duration_ms = (time.monotonic() - start) * 1000
            self.monitor.track("code_task_duration_ms", duration_ms)

            outputs: dict[str, Any] = {
                "result": exec_result.result,
                "logs": exec_result.logs,
                "execution_time_ms": duration_ms,
            }

            if exec_result.success:
                self.monitor.track("code_task_success", 1)
                return ExecuteTaskResult(
                    task_id=request.task_id,
                    status=TaskStatus.SUCCESS,
                    outputs=outputs,
                    duration_ms=duration_ms,
                )

            self.monitor.track("code_task_failure", 1)
            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.ERROR,
                outputs=outputs,
                error=exec_result.error or "Script execution failed",
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = (time.monotonic() - start) * 1000
            self.logger.error("Code task failed", task_id=request.task_id, error=str(e))
            self.monitor.track("code_task_error", 1)
            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.ERROR,
                error=str(e),
                outputs={"error_type": type(e).__name__},
                duration_ms=duration_ms,
            )

    @staticmethod
    def _normalize_items(input_data: dict[str, Any]) -> list[Any]:
        """Derive the `items` list exposed to the script.

        If the upstream payload carries an ``items`` array, use it directly
        (n8n style). Otherwise wrap the whole payload as a single item so the
        script always receives a list to iterate.
        """
        candidate = input_data.get("items")
        if isinstance(candidate, list):
            return candidate
        if not input_data:
            return []
        return [input_data]

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
