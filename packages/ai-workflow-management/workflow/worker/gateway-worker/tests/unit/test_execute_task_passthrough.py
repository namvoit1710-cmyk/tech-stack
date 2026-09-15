"""The gateway-worker's passthrough ``/execute`` use case.

A function-only worker had no ``/execute`` route, so a TASK node carrying a
``_function_call`` marker fired the gateway call (in the control-plane) but then
404'd when the node dispatched its task to the worker. This use case echoes the
already-resolved input — which carries the spliced gateway response — back as
the node's output so the node COMPLETES.
"""
from __future__ import annotations

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)

from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
)


def test_passthrough_echoes_input_as_output_and_succeeds(logger):
    uc = ExecuteTaskUseCase(logger=logger)
    gateway_response = {"status_code": 200, "headers": {}, "body": {"ok": True}}
    out = uc.execute(
        ExecuteTaskCommand(
            task_id="t1",
            action="execute",
            inputs={"result": gateway_response},
        )
    )

    assert out.task_id == "t1"
    assert out.status == TaskStatus.SUCCESS
    # The spliced gateway response is surfaced verbatim as the node output.
    assert out.outputs == {"result": gateway_response}
    assert out.error is None


def test_passthrough_output_is_a_copy_not_the_input_dict(logger):
    uc = ExecuteTaskUseCase(logger=logger)
    inputs = {"result": {"status_code": 200}}
    out = uc.execute(ExecuteTaskCommand(task_id="t2", action="execute", inputs=inputs))
    out.outputs["injected"] = True
    assert "injected" not in inputs  # top-level dict copied, caller input untouched
