"""gRPC transport — the exec core must reach a thread, not the grpc.aio loop.

Skipped unless the generated protos are importable: TRANSPORT_MODE=grpc is
opt-in (app_config defaults to "http") and ``workflow_proto`` is not installed
in every dev venv. That absence is exactly why the defect below shipped
untested.
"""

import pytest

pytest.importorskip("workflow_proto.v1.worker_execution_pb2")

from workflow_proto.v1 import worker_execution_pb2 as pb  # noqa: E402

from worker_sdk.layer1_domain.entities.task_response import TaskResponse  # noqa: E402
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus  # noqa: E402
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (  # noqa: E402
    ExecuteTaskUseCase,
)
from worker_sdk.layer3_adapters.controllers.grpc.worker_execution_servicer import (  # noqa: E402
    WorkerExecutionServicer,
)


class _Logger:
    def info(self, *a, **k): pass
    def error(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def debug(self, *a, **k): pass


class _Monitor:
    def track(self, *a, **k): pass


class _Executor:
    def execute(self, request):
        return TaskResponse(
            task_id=request.task_id,
            status=TaskStatus.SUCCESS,
            outputs={"result": "done"},
        )


def _servicer():
    uc = ExecuteTaskUseCase(
        logger=_Logger(), monitor=_Monitor(), task_executor=_Executor(),
    )
    return WorkerExecutionServicer({"execute_task": uc})


@pytest.mark.asyncio
async def test_execute_reaches_the_exec_core_off_the_loop():
    """Regression. The servicer used to ``await`` the SYNC use case:

      * ``await <ExecuteTaskResult>`` is unconditionally a TypeError, caught by
        the broad ``except Exception`` and returned as a generic ERROR — so
        EVERY gRPC Execute failed, with the real cause buried in the message;
      * and the call itself ran inline, blocking the grpc.aio loop for the
        whole handler.

    Both are fixed by dispatching through ``asyncio.to_thread``. The exec
    core's own ``assert_off_event_loop`` now also refuses the inline form, so a
    regression here fails loudly instead of returning ERROR.
    """
    resp = await _servicer().Execute(
        pb.ExecuteRequest(task_id="t1", action="run"), None,
    )
    assert resp.status == TaskStatus.SUCCESS.value
    assert resp.error == ""
    assert resp.outputs["result"] == "done"


@pytest.mark.asyncio
async def test_execute_with_progress_yields_a_successful_result():
    """ExecuteWithProgress delegates to Execute, so it inherited the same bug:
    its terminal event carried the ERROR response."""
    events = [
        e async for e in _servicer().ExecuteWithProgress(
            pb.ExecuteRequest(task_id="t2", action="run"), None,
        )
    ]
    assert [e.event_type for e in events] == ["progress", "result"]
    assert events[-1].result.status == TaskStatus.SUCCESS.value
    assert events[-1].result.error == ""
