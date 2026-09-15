"""Tests for the pure node-status aggregation rule."""

from app.layer1_domain.entities.task import Task
from app.layer2_application.read.node_status_aggregator import aggregate_node_status
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus


def _task(status: TaskStatus) -> Task:
    return Task(run_id="r1", node_id="n1", status=status)


def test_skipped_takes_precedence():
    assert aggregate_node_status("n1", [_task(TaskStatus.RUNNING)], {"n1"}, RunStatus.RUNNING) == "SKIPPED"


def test_no_tasks_in_flight_run_is_not_started():
    # Run is alive but this node hasn't been reached — show NOT_STARTED, not
    # the run's status (which would falsely animate the node as RUNNING).
    assert aggregate_node_status("n1", [], set(), RunStatus.RUNNING) == "NOT_STARTED"
    assert aggregate_node_status("n1", [], set(), RunStatus.PENDING) == "NOT_STARTED"
    assert aggregate_node_status("n1", [], set(), RunStatus.PAUSED) == "NOT_STARTED"
    assert aggregate_node_status("n1", [], set(), RunStatus.CREATED) == "NOT_STARTED"


def test_no_tasks_terminal_run_is_skipped():
    # Run ended without ever reaching this node — it didn't fail, it was bypassed.
    assert aggregate_node_status("n1", [], set(), RunStatus.COMPLETED) == "SKIPPED"
    assert aggregate_node_status("n1", [], set(), RunStatus.FAILED) == "SKIPPED"
    assert aggregate_node_status("n1", [], set(), RunStatus.CANCELLED) == "SKIPPED"


def test_no_tasks_accepts_string_run_status():
    assert aggregate_node_status("n1", [], set(), "RUNNING") == "NOT_STARTED"
    assert aggregate_node_status("n1", [], set(), "FAILED") == "SKIPPED"


def test_any_failed_wins():
    tasks = [_task(TaskStatus.COMPLETED), _task(TaskStatus.FAILED), _task(TaskStatus.RUNNING)]
    assert aggregate_node_status("n1", tasks, set(), RunStatus.RUNNING) == "FAILED"


def test_all_completed():
    tasks = [_task(TaskStatus.COMPLETED), _task(TaskStatus.COMPLETED)]
    assert aggregate_node_status("n1", tasks, set(), RunStatus.RUNNING) == "COMPLETED"


def test_any_running():
    tasks = [_task(TaskStatus.COMPLETED), _task(TaskStatus.RUNNING), _task(TaskStatus.PENDING)]
    assert aggregate_node_status("n1", tasks, set(), RunStatus.RUNNING) == "RUNNING"


def test_any_pending_when_no_running():
    tasks = [_task(TaskStatus.READY), _task(TaskStatus.PENDING)]
    assert aggregate_node_status("n1", tasks, set(), RunStatus.RUNNING) == "PENDING"


def test_fallback_to_first_task_status():
    tasks = [_task(TaskStatus.READY), _task(TaskStatus.READY)]
    assert aggregate_node_status("n1", tasks, set(), RunStatus.RUNNING) == "READY"


def test_other_node_skipped_does_not_affect():
    tasks = [_task(TaskStatus.COMPLETED)]
    assert aggregate_node_status("n1", tasks, {"n2"}, RunStatus.RUNNING) == "COMPLETED"
