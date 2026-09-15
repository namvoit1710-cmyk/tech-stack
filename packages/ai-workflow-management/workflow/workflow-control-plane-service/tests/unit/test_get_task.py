"""Tests for GetTaskUseCase."""

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.get_task.use_cases.get_task_use_case import (
    GetTaskUseCase,
    GetTaskCommand,
)
from tests.conftest import DummyLogger


@pytest.fixture
def usecase(task_repo):
    return GetTaskUseCase(logger=DummyLogger(), task_repo=task_repo)


@pytest.fixture
def pending_input_task():
    return Task(
        id="task-input-1",
        run_id="run-1",
        node_id="input-node-1",
        status=TaskStatus.PENDING,
        resolved_input={"prefilled_name": "Alice"},
        output_metadata={
            "node_type": "INPUT",
            "input_schema": [
                {"key": "name", "type": "string", "label": "Full Name", "required": True},
                {"key": "email", "type": "string", "label": "Email Address"},
            ],
            "instruction": "Please fill in your details.",
            "prefilled": {"prefilled_name": "Alice"},
            "timeout_minutes": 60,
        },
    )


@pytest.fixture
def pending_human_action_task():
    return Task(
        id="task-ha-1",
        run_id="run-1",
        node_id="ha-node-1",
        status=TaskStatus.PENDING,
        output_metadata={
            "node_type": "HUMAN_ACTION",
            "instruction": "Review the purchase order and approve or reject.",
            "input_schema": [{"key": "comments", "type": "string", "label": "Comments"}],
            "confirm_label": "Approve",
            "reject_label": "Reject",
            "prefilled": {"po_amount": 5000, "vendor": "Acme Corp"},
        },
    )


async def test_get_task_returns_full_details(usecase, task_repo, pending_input_task):
    task_repo.save(pending_input_task)
    result = await usecase.execute(GetTaskCommand(run_id="run-1", task_id="task-input-1"))

    assert result.id == "task-input-1"
    assert result.run_id == "run-1"
    assert result.node_id == "input-node-1"
    assert result.status == "PENDING"
    assert result.output_metadata["node_type"] == "INPUT"
    assert len(result.output_metadata["input_schema"]) == 2
    assert result.output_metadata["prefilled"]["prefilled_name"] == "Alice"
    assert result.error is None


async def test_get_task_human_action(usecase, task_repo, pending_human_action_task):
    task_repo.save(pending_human_action_task)
    result = await usecase.execute(GetTaskCommand(run_id="run-1", task_id="task-ha-1"))

    assert result.id == "task-ha-1"
    assert result.status == "PENDING"
    assert result.output_metadata["node_type"] == "HUMAN_ACTION"
    assert result.output_metadata["confirm_label"] == "Approve"
    assert result.output_metadata["reject_label"] == "Reject"
    assert result.output_metadata["instruction"] == "Review the purchase order and approve or reject."


async def test_get_task_not_found(usecase):
    result = await usecase.execute(GetTaskCommand(run_id="run-1", task_id="nonexistent"))
    assert result.error == "Task not found"
    assert result.id == ""


async def test_get_task_wrong_run_id(usecase, task_repo, pending_input_task):
    task_repo.save(pending_input_task)
    result = await usecase.execute(GetTaskCommand(run_id="wrong-run", task_id="task-input-1"))
    assert result.error == "Task does not belong to this run"
    assert result.id == ""


async def test_get_task_completed_task(usecase, task_repo):
    task = Task(
        id="task-done-1",
        run_id="run-1",
        node_id="n1",
        status=TaskStatus.COMPLETED,
        output_ref="ref-123",
        output_metadata={"result": "success"},
    )
    task_repo.save(task)
    result = await usecase.execute(GetTaskCommand(run_id="run-1", task_id="task-done-1"))

    assert result.id == "task-done-1"
    assert result.status == "COMPLETED"
    assert result.output_ref == "ref-123"


async def test_get_task_includes_resolved_input(usecase, task_repo, pending_input_task):
    task_repo.save(pending_input_task)
    result = await usecase.execute(GetTaskCommand(run_id="run-1", task_id="task-input-1"))
    assert result.resolved_input == {"prefilled_name": "Alice"}
