from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)

from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    CODE_INPUT_SCHEMA,
    CODE_OUTPUT_SCHEMA,
)
from app.layer2_application.interfaces.code_executor_interface import CodeExecutionResult


class FakeExecutor:
    """Records the last call and returns a configurable result."""

    def __init__(self, result: CodeExecutionResult):
        self._result = result
        self.calls = []

    def run(self, language, code, items, input_data, mode, timeout_seconds):
        self.calls.append({
            "language": language, "code": code, "items": items,
            "input_data": input_data, "mode": mode, "timeout_seconds": timeout_seconds,
        })
        return self._result


def _uc(logger, monitor, result):
    return ExecuteTaskUseCase(
        logger=logger, monitor=monitor, code_executor=FakeExecutor(result)
    )


class TestSuccess:
    def test_basic_success(self, logger, monitor):
        uc = _uc(logger, monitor, CodeExecutionResult(success=True, result=[1, 2], logs=["hi"]))
        res = uc.execute(ExecuteTaskCommand(
            task_id="t1", action="code",
            inputs={"language": "python", "code": "return items", "items": [1, 2]},
        ))
        assert res.status == TaskStatus.SUCCESS
        assert res.outputs["result"] == [1, 2]
        assert res.outputs["logs"] == ["hi"]
        assert res.duration_ms > 0

    def test_executor_receives_normalized_items(self, logger, monitor):
        fake = FakeExecutor(CodeExecutionResult(success=True, result=None))
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, code_executor=fake)
        uc.execute(ExecuteTaskCommand(
            task_id="t2", action="code",
            inputs={"language": "python", "code": "return 1", "foo": "bar"},
        ))
        call = fake.calls[0]
        # config keys stripped from data; rest exposed as input + wrapped item
        assert call["input_data"] == {"foo": "bar"}
        assert call["items"] == [{"foo": "bar"}]


class TestValidation:
    def test_missing_language(self, logger, monitor):
        uc = _uc(logger, monitor, CodeExecutionResult(success=True))
        res = uc.execute(ExecuteTaskCommand(
            task_id="t3", action="code", inputs={"code": "return 1"},
        ))
        assert res.status == TaskStatus.ERROR
        assert "language" in res.error.lower()

    def test_invalid_language(self, logger, monitor):
        uc = _uc(logger, monitor, CodeExecutionResult(success=True))
        res = uc.execute(ExecuteTaskCommand(
            task_id="t4", action="code", inputs={"language": "ruby", "code": "x"},
        ))
        assert res.status == TaskStatus.ERROR
        assert "language" in res.error.lower()

    def test_missing_code(self, logger, monitor):
        uc = _uc(logger, monitor, CodeExecutionResult(success=True))
        res = uc.execute(ExecuteTaskCommand(
            task_id="t5", action="code", inputs={"language": "python", "code": "  "},
        ))
        assert res.status == TaskStatus.ERROR
        assert "code" in res.error.lower()

    def test_timeout_out_of_range(self, logger, monitor):
        uc = _uc(logger, monitor, CodeExecutionResult(success=True))
        res = uc.execute(ExecuteTaskCommand(
            task_id="t6", action="code",
            inputs={"language": "python", "code": "return 1", "timeout_seconds": 9999},
        ))
        assert res.status == TaskStatus.ERROR
        assert "timeout" in res.error.lower()

    def test_no_executor_configured(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, code_executor=None)
        res = uc.execute(ExecuteTaskCommand(
            task_id="t7", action="code",
            inputs={"language": "python", "code": "return 1"},
        ))
        assert res.status == TaskStatus.ERROR


class TestScriptFailure:
    def test_script_error_maps_to_error_status(self, logger, monitor):
        uc = _uc(logger, monitor, CodeExecutionResult(success=False, error="boom", logs=["x"]))
        res = uc.execute(ExecuteTaskCommand(
            task_id="t8", action="code",
            inputs={"language": "python", "code": "return 1"},
        ))
        assert res.status == TaskStatus.ERROR
        assert res.error == "boom"
        assert res.outputs["logs"] == ["x"]


class TestMetrics:
    def test_success_metrics(self, logger, monitor):
        uc = _uc(logger, monitor, CodeExecutionResult(success=True))
        uc.execute(ExecuteTaskCommand(
            task_id="t9", action="code",
            inputs={"language": "python", "code": "return 1"},
        ))
        names = [m[0] for m in monitor.tracked]
        assert "code_task_requests" in names
        assert "code_task_success" in names

    def test_error_metrics(self, logger, monitor):
        uc = _uc(logger, monitor, CodeExecutionResult(success=True))
        uc.execute(ExecuteTaskCommand(task_id="t10", action="code", inputs={}))
        names = [m[0] for m in monitor.tracked]
        assert "code_task_error" in names


class TestSchemas:
    def test_input_schema_keys(self):
        keys = [f["key"] for f in CODE_INPUT_SCHEMA]
        assert "language" in keys
        assert "code" in keys
        assert "mode" in keys
        assert "timeout_seconds" in keys

    def test_output_schema_keys(self):
        keys = [f["key"] for f in CODE_OUTPUT_SCHEMA]
        assert "result" in keys
        assert "logs" in keys
        assert "execution_time_ms" in keys
