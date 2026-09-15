"""Passthrough ``/execute`` for the gateway-worker.

The gateway-worker's real work lives in its functions (``call_internal`` /
``call_external``), invoked via ``POST /api/v1/functions/{name}/invoke``. A
workflow reaches those by putting a ``_function_call`` marker in a TASK node's
input: the control-plane fires the function during input resolution and splices
the gateway response back into the node's resolved input (see ``TaskHandler.
_resolve_function_calls``).

A TASK node then *also* dispatches a task to its worker's ``/execute`` route
(``task_handler.py`` lines 76-84, unconditional). Being function-only, the
gateway-worker had no such route, so the node 404'd and FAILED even though the
gateway call had already succeeded. Adding this use case makes the SDK mount
``/execute`` + ``/execute-async``; it simply surfaces the already-resolved input
(which now carries the gateway response) as the node's output, so the node
COMPLETES and the response flows to downstream nodes.
"""

import time
from typing import Any

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.interfaces.logger_interface import ILogger
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
    ExecuteTaskResult,
)


class ExecuteTaskUseCase:
    """Echo the resolved task input back as the node's output.

    The gateway call already happened in the control-plane (via the
    ``_function_call`` marker), so there is no further work to do here — the
    input, which now carries the gateway response, becomes the output.
    """

    def __init__(self, logger: ILogger, **kwargs: Any) -> None:
        self.logger = logger

    def execute(self, request: ExecuteTaskCommand) -> ExecuteTaskResult:
        start = time.monotonic()
        self.logger.info(
            "gateway-worker passthrough execute",
            task_id=request.task_id,
            input_keys=sorted(request.inputs.keys()),
        )
        return ExecuteTaskResult(
            task_id=request.task_id,
            status=TaskStatus.SUCCESS,
            outputs=dict(request.inputs),
            duration_ms=(time.monotonic() - start) * 1000,
        )
