"""Pure domain service: derive a node's effective status from its tasks.

Single source of truth for the rule "given a node's task list and the run's
context, what is this node's status?" — used by both the heavy node-execution
endpoint and the lightweight bulk-status endpoint so they cannot disagree.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus


SKIPPED_STATUS = "SKIPPED"
NOT_STARTED_STATUS = "NOT_STARTED"

_TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
}


def aggregate_node_status(
    node_id: str,
    tasks: Iterable[Task],
    skipped_nodes: set[str],
    run_status: RunStatus | str,
) -> str:
    """Return the effective status string for a node.

    Rules (in order):
      1. node_id in skipped_nodes              -> "SKIPPED"
      2. no tasks for the node, run terminal   -> "SKIPPED" (run ended without reaching node)
      3. no tasks for the node, run in flight  -> "NOT_STARTED"
      4. any task FAILED                       -> "FAILED"
      5. all tasks COMPLETED                   -> "COMPLETED"
      6. any task RUNNING                      -> "RUNNING"
      7. any task PENDING                      -> "PENDING"
      8. fallback: first task's status string  (e.g. READY, WAITING, RETRYING)
    """
    if node_id in skipped_nodes:
        return SKIPPED_STATUS

    task_list = list(tasks)
    if not task_list:
        run_value = run_status.value if isinstance(run_status, RunStatus) else str(run_status)
        if run_value in _TERMINAL_RUN_STATUSES:
            return SKIPPED_STATUS
        return NOT_STARTED_STATUS

    statuses = [t.status for t in task_list]
    if any(s == TaskStatus.FAILED for s in statuses):
        return TaskStatus.FAILED.value
    if all(s == TaskStatus.COMPLETED for s in statuses):
        return TaskStatus.COMPLETED.value
    if any(s == TaskStatus.RUNNING for s in statuses):
        return TaskStatus.RUNNING.value
    if any(s == TaskStatus.PENDING for s in statuses):
        return TaskStatus.PENDING.value

    first = task_list[0].status
    return first.value if isinstance(first, TaskStatus) else str(first)
