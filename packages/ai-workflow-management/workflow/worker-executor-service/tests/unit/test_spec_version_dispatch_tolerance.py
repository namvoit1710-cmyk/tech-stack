"""SA-1490 Phase 1 — the executor TOLERATES a pinned `spec_version` from WCP
dispatch (accept + plumb onto ExecutionTask), without yet honoring it.

Safety regression guard: the Event-Mesh intake is ``ExecuteTaskInput(**data)``
(main.py) and the HTTP intake is ``ExecuteTaskInput(**payload.model_dump())``
(task_controller.py). Before this change, once WCP started sending a
``spec_version`` key those calls raised ``TypeError: unexpected keyword
argument`` — taking down task execution. These tests pin that it no longer does.
"""
from __future__ import annotations

from app.layer1_domain.entities.execution_task import ExecutionTask
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import ExecuteTaskInput
from app.layer3_adapters.controllers.restful.v1.task_controller import ExecuteTaskInputDto


def test_execute_task_input_tolerates_spec_version_kwarg():
    """Event-Mesh path: ExecuteTaskInput(**data) with a spec_version key must not raise."""
    data = {
        "task_id": "t1", "run_id": "r1", "node_id": "n1",
        "worker_type": "http-request", "spec_version": "2.1.0",
        "payload": {}, "input": {}, "node_config": {}, "idempotency_key": "k",
    }
    inp = ExecuteTaskInput(**data)
    assert inp.spec_version == "2.1.0"


def test_execute_task_input_defaults_spec_version_empty():
    """Backward-compat: dispatches without spec_version still work (default '')."""
    inp = ExecuteTaskInput(task_id="t", worker_type="w")
    assert inp.spec_version == ""


def test_execution_task_carries_spec_version():
    assert ExecutionTask(worker_type="w", spec_version="1.2.3").spec_version == "1.2.3"
    assert ExecutionTask(worker_type="w").spec_version == ""


def test_http_dto_round_trips_spec_version_into_input():
    """HTTP path: DTO.model_dump() -> ExecuteTaskInput(**...) carries the pin."""
    dto = ExecuteTaskInputDto(task_id="t", worker_type="w", spec_version="3.0.0")
    inp = ExecuteTaskInput(**dto.model_dump())
    assert inp.spec_version == "3.0.0"
    # and default when omitted
    dto2 = ExecuteTaskInputDto(task_id="t", worker_type="w")
    assert ExecuteTaskInput(**dto2.model_dump()).spec_version == ""
