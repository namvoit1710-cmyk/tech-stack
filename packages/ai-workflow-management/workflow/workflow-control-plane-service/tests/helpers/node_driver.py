"""Synchronous single-node driver for unit/integration tests.

Drives one task through the engine's uniform execution path
(``OrchestrationEngine._exec_uniform``) without a queue, worker, or DAG
walking. This is the test-only helper that used to live on the
production engine as ``OrchestrationEngine.dispatch_or_handle_task`` —
moved here so the engine carries no test-only entry point.
"""
from __future__ import annotations

from typing import Any


async def drive_node(
    engine: Any,
    task: Any,
    run: Any,
    workflow: Any,
    topology: Any,
) -> None:
    """Persist *task* and run its node once through ``_exec_uniform``."""
    node = workflow.get_node(task.node_id)
    if node is None:
        return
    await engine._task_repo.asave(task)
    await engine._exec_uniform(task, node, run, workflow, topology)
