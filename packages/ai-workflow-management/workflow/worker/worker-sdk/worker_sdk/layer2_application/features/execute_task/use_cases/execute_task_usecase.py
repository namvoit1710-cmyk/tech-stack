import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from worker_sdk.layer2_application.interfaces.logger_interface import ILogger
from worker_sdk.layer2_application.interfaces.monitor_interface import IMonitor
from worker_sdk.layer2_application.interfaces.task_executor_interface import ITaskExecutor
from worker_sdk.layer1_domain.entities.task_request import TaskRequest
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus


@dataclass
class ExecuteTaskCommand:
    task_id: str
    action: str
    inputs: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    correlation_id: Optional[str] = None


@dataclass
class ExecuteTaskResult:
    task_id: str
    status: TaskStatus
    outputs: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration_ms: float = 0.0
    output_reference: str = ""


def assert_off_event_loop() -> None:
    """Refuse to run the exec core ON an asyncio event loop.

    ``execute`` is deliberately SYNC: a worker handler may burn CPU or make
    blocking calls for minutes. Every transport therefore has to hand it to a
    thread (``asyncio.to_thread``) — and when one forgets, the failure is
    silent and remote rather than local:

      * PULL — lease renewal and the heartbeat are asyncio tasks on that same
        loop, so neither gets scheduled. The lease expires, the executor
        re-enqueues the task to another worker, and this worker's callback is
        later rejected on a dead lease_token. Surfaces minutes later as a
        duplicate execution or a DLQ entry.
      * SERVER — every other in-flight request on the pod stalls behind it.

    Two lines here turn all of that into an immediate, local, actionable error.
    Sync callers (tests, CLI, any non-async embedder) have no running loop and
    pass straight through.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # correct: no loop on this thread
    raise RuntimeError(
        # ASCII only: this message is raised on Windows-hosted workers whose
        # console/log stream may be cp1252.
        "worker-sdk: ExecuteTaskUseCase.execute() was called ON an asyncio "
        "event loop. It is a sync use case and will block the loop, so lease "
        "renewal, heartbeats and other requests cannot run. Dispatch it with "
        "asyncio.to_thread(exec_uc.execute, cmd)."
    )


class ExecuteTaskUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        task_executor: Optional[ITaskExecutor] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.monitor = monitor
        self.task_executor = task_executor

    def execute(self, request: ExecuteTaskCommand) -> ExecuteTaskResult:
        # Invariant for every transport (PULL / SERVER / gRPC): the exec core
        # never runs on the event loop. Deliberately OUTSIDE the try below —
        # a misuse must propagate, not be flattened into an ERROR result.
        assert_off_event_loop()
        self.logger.info(
            "Executing task",
            task_id=request.task_id,
            action=request.action,
        )
        self.monitor.track("task_execute_requests", 1)

        start = time.monotonic()

        try:
            if self.task_executor is not None:
                task_request = TaskRequest(
                    task_id=request.task_id,
                    action=request.action,
                    inputs=request.inputs,
                    parameters=request.parameters,
                    correlation_id=request.correlation_id,
                )
                task_response = self.task_executor.execute(task_request)
                duration_ms = (time.monotonic() - start) * 1000

                self.monitor.track("task_execute_duration_ms", duration_ms)
                self.monitor.track("task_execute_success", 1)

                return ExecuteTaskResult(
                    task_id=task_response.task_id,
                    status=task_response.status,
                    outputs=task_response.outputs,
                    error=task_response.error,
                    duration_ms=duration_ms,
                )
            else:
                # Placeholder: no executor registered
                duration_ms = (time.monotonic() - start) * 1000
                self.logger.warning("No task executor registered, returning placeholder")
                return ExecuteTaskResult(
                    task_id=request.task_id,
                    status=TaskStatus.SUCCESS,
                    outputs={"message": "placeholder - no executor registered"},
                    duration_ms=duration_ms,
                )

        except Exception as e:
            duration_ms = (time.monotonic() - start) * 1000
            self.logger.error("Task execution failed", task_id=request.task_id, error=str(e))
            self.monitor.track("task_execute_error", 1)
            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.ERROR,
                error=str(e),
                duration_ms=duration_ms,
            )
