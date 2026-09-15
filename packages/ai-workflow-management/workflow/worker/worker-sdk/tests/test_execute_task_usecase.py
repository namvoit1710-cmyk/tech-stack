import asyncio

import pytest

from worker_sdk.layer1_domain.entities.task_request import TaskRequest
from worker_sdk.layer1_domain.entities.task_response import TaskResponse
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase as UseCase,
    ExecuteTaskCommand,
    assert_off_event_loop,
)


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


class DummyTaskExecutor:
    def execute(self, request: TaskRequest) -> TaskResponse:
        return TaskResponse(
            task_id=request.task_id,
            status=TaskStatus.SUCCESS,
            outputs={"result": "done"},
        )


class FailingTaskExecutor:
    def execute(self, request: TaskRequest) -> TaskResponse:
        raise RuntimeError("executor failed")


def test_execute_task_without_executor_returns_placeholder():
    logger = DummyLogger()
    monitor = DummyMonitor()
    use_case = UseCase(logger=logger, monitor=monitor)
    result = use_case.execute(ExecuteTaskCommand(task_id="t1", action="run"))
    assert result.task_id == "t1"
    assert result.status == TaskStatus.SUCCESS
    assert "placeholder" in result.outputs["message"]


def test_execute_task_with_executor_returns_response():
    logger = DummyLogger()
    monitor = DummyMonitor()
    executor = DummyTaskExecutor()
    use_case = UseCase(logger=logger, monitor=monitor, task_executor=executor)
    result = use_case.execute(ExecuteTaskCommand(task_id="t2", action="process"))
    assert result.task_id == "t2"
    assert result.status == TaskStatus.SUCCESS
    assert result.outputs == {"result": "done"}
    assert result.duration_ms >= 0


def test_execute_task_with_failing_executor_returns_error():
    logger = DummyLogger()
    monitor = DummyMonitor()
    executor = FailingTaskExecutor()
    use_case = UseCase(logger=logger, monitor=monitor, task_executor=executor)
    result = use_case.execute(ExecuteTaskCommand(task_id="t3", action="fail"))
    assert result.task_id == "t3"
    assert result.status == TaskStatus.ERROR
    assert result.error == "executor failed"


def test_execute_task_tracks_metrics():
    logger = DummyLogger()
    monitor = DummyMonitor()
    use_case = UseCase(logger=logger, monitor=monitor)
    use_case.execute(ExecuteTaskCommand(task_id="t4", action="run"))
    metric_names = [m[0] for m in monitor.metrics]
    assert "task_execute_requests" in metric_names


# --- event-loop invariant (covers PULL + SERVER + gRPC in one place) ---
#
# The exec core is sync and may run for minutes. Every transport must hand it
# to a thread; when one forgets, the failure is silent and remote (PULL loses
# lease renewal + heartbeat -> duplicate execution; SERVER stalls every other
# request). The assert makes it local and immediate.


class _BlockingExecutor:
    def __init__(self):
        self.called = False

    def execute(self, request: TaskRequest) -> TaskResponse:
        self.called = True
        return TaskResponse(task_id=request.task_id, status=TaskStatus.SUCCESS)


@pytest.mark.asyncio
async def test_execute_refuses_to_run_on_the_event_loop():
    ex = _BlockingExecutor()
    use_case = UseCase(logger=DummyLogger(), monitor=DummyMonitor(), task_executor=ex)
    with pytest.raises(RuntimeError, match="event loop"):
        use_case.execute(ExecuteTaskCommand(task_id="t5", action="run"))
    assert ex.called is False, "the handler ran anyway — the guard is too late"


@pytest.mark.asyncio
async def test_execute_refusal_is_not_swallowed_into_an_error_result():
    """The guard sits OUTSIDE the try/except, so a misuse propagates instead of
    being flattened into ExecuteTaskResult(status=ERROR) — which would look
    like a failing handler and send the reader hunting in the wrong place."""
    use_case = UseCase(logger=DummyLogger(), monitor=DummyMonitor(),
                       task_executor=FailingTaskExecutor())
    with pytest.raises(RuntimeError):
        use_case.execute(ExecuteTaskCommand(task_id="t6", action="run"))


@pytest.mark.asyncio
async def test_execute_is_fine_when_dispatched_to_a_thread():
    """The supported path — what every transport actually does."""
    use_case = UseCase(logger=DummyLogger(), monitor=DummyMonitor(),
                       task_executor=DummyTaskExecutor())
    result = await asyncio.to_thread(
        use_case.execute, ExecuteTaskCommand(task_id="t7", action="run"),
    )
    assert result.status == TaskStatus.SUCCESS
    assert result.outputs == {"result": "done"}


def test_assert_off_event_loop_is_a_noop_for_sync_callers():
    """Tests, CLIs and non-async embedders must pass straight through."""
    assert_off_event_loop()   # no running loop here -> returns
