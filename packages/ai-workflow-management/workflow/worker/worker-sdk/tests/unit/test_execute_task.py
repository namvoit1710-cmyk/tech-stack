from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    ExecuteTaskCommand,
    ExecuteTaskResult,
)
from worker_sdk.layer1_domain.entities.task_request import TaskRequest
from worker_sdk.layer1_domain.entities.task_response import TaskResponse
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus


class DummyLogger:
    def __init__(self):
        self.events = []

    def info(self, message: str, **kwargs):
        self.events.append(("info", message, kwargs))

    def error(self, message: str, **kwargs):
        self.events.append(("error", message, kwargs))

    def warning(self, message: str, **kwargs):
        self.events.append(("warning", message, kwargs))

    def debug(self, message: str, **kwargs):
        self.events.append(("debug", message, kwargs))


class DummyMonitor:
    def __init__(self):
        self.metrics = []

    def track(self, metric: str, value: float):
        self.metrics.append((metric, value))


class DummyExecutor:
    def execute(self, request: TaskRequest) -> TaskResponse:
        return TaskResponse(
            task_id=request.task_id,
            status=TaskStatus.SUCCESS,
            outputs={"echo": request.inputs.get("text", "")},
        )


class FailingExecutor:
    def execute(self, request: TaskRequest) -> TaskResponse:
        raise RuntimeError("something went wrong")


def test_execute_task_with_executor():
    logger = DummyLogger()
    monitor = DummyMonitor()
    executor = DummyExecutor()
    uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, task_executor=executor)

    result = uc.execute(
        ExecuteTaskCommand(
            task_id="t-1",
            action="echo",
            inputs={"text": "hello"},
        )
    )

    assert result.status == TaskStatus.SUCCESS
    assert result.task_id == "t-1"
    assert result.outputs["echo"] == "hello"
    assert result.duration_ms >= 0


def test_execute_task_without_executor():
    logger = DummyLogger()
    monitor = DummyMonitor()
    uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)

    result = uc.execute(
        ExecuteTaskCommand(task_id="t-2", action="noop")
    )

    assert result.status == TaskStatus.SUCCESS
    assert "placeholder" in result.outputs.get("message", "")


def test_execute_task_error_handling():
    logger = DummyLogger()
    monitor = DummyMonitor()
    executor = FailingExecutor()
    uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, task_executor=executor)

    result = uc.execute(
        ExecuteTaskCommand(task_id="t-3", action="fail")
    )

    assert result.status == TaskStatus.ERROR
    assert "something went wrong" in result.error
    assert any(m[0] == "task_execute_error" for m in monitor.metrics)


def test_execute_task_with_correlation_id():
    logger = DummyLogger()
    monitor = DummyMonitor()
    executor = DummyExecutor()
    uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, task_executor=executor)

    result = uc.execute(
        ExecuteTaskCommand(
            task_id="t-4",
            action="echo",
            inputs={"text": "corr"},
            parameters={"p1": "v1"},
            correlation_id="corr-123",
        )
    )

    assert result.task_id == "t-4"
    assert result.status == TaskStatus.SUCCESS


def test_execute_task_tracks_request_metric():
    logger = DummyLogger()
    monitor = DummyMonitor()
    uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)

    uc.execute(ExecuteTaskCommand(task_id="t-5", action="noop"))

    metric_names = [m[0] for m in monitor.metrics]
    assert "task_execute_requests" in metric_names


def test_execute_task_tracks_success_metrics():
    logger = DummyLogger()
    monitor = DummyMonitor()
    executor = DummyExecutor()
    uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, task_executor=executor)

    uc.execute(ExecuteTaskCommand(task_id="t-6", action="echo"))

    metric_names = [m[0] for m in monitor.metrics]
    assert "task_execute_duration_ms" in metric_names
    assert "task_execute_success" in metric_names


def test_execute_task_logs_info_on_start():
    logger = DummyLogger()
    monitor = DummyMonitor()
    uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)

    uc.execute(ExecuteTaskCommand(task_id="t-7", action="run"))

    assert any(msg == "Executing task" for _, msg, _ in logger.events)


def test_execute_task_logs_warning_no_executor():
    logger = DummyLogger()
    monitor = DummyMonitor()
    uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)

    uc.execute(ExecuteTaskCommand(task_id="t-8", action="run"))

    assert any(
        level == "warning" and "No task executor" in msg
        for level, msg, _ in logger.events
    )


def test_execute_task_logs_error_on_failure():
    logger = DummyLogger()
    monitor = DummyMonitor()
    executor = FailingExecutor()
    uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, task_executor=executor)

    uc.execute(ExecuteTaskCommand(task_id="t-9", action="fail"))

    assert any(
        level == "error" and "Task execution failed" in msg
        for level, msg, _ in logger.events
    )


def test_execute_task_input_defaults():
    inp = ExecuteTaskCommand(task_id="t-10", action="x")
    assert inp.inputs == {}
    assert inp.parameters == {}
    assert inp.correlation_id is None


def test_execute_task_output_defaults():
    out = ExecuteTaskResult(task_id="t-11", status=TaskStatus.SUCCESS)
    assert out.outputs == {}
    assert out.error is None
    assert out.duration_ms == 0.0


def test_execute_task_accepts_extra_kwargs():
    """ExecuteTaskUseCase accepts **kwargs for forward-compatibility."""
    logger = DummyLogger()
    monitor = DummyMonitor()
    uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, unknown_dep="ignored")
    result = uc.execute(ExecuteTaskCommand(task_id="t-12", action="noop"))
    assert result.status == TaskStatus.SUCCESS


## File reference resolution tests are in test_file_ref_resolver.py.
## Resolution now happens in routes.py before the use case is called.
