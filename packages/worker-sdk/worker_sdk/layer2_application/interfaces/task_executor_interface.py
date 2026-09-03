from typing import Protocol

from worker_sdk.layer1_domain.entities.task_request import TaskRequest
from worker_sdk.layer1_domain.entities.task_response import TaskResponse


class ITaskExecutor(Protocol):
    def execute(self, request: TaskRequest) -> TaskResponse:
        """Execute a task. Concrete workers implement this."""
        ...
