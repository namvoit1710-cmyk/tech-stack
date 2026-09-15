"""Test helper that drives the orchestration queue synchronously.

Use this to exercise the queued execution path from unit/integration tests
without spinning up the full FastAPI lifespan worker. After calling an
engine entry point (``orchestrate_start``, ``orchestrate_execute_node``,
etc.), call ``run_until_idle`` to drain every action the engine enqueued.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.layer2_application.interfaces.orchestration_queue_interface import (
    ContinueRunAction,
    ExecuteNodeAction,
    FailTaskAction,
    LoopIterationAction,
    ResumeRunAction,
    ResumeTaskAction,
    StartRunAction,
)
from app.layer2_application.orchestration_queue.memory_queue import (
    MemoryOrchestrationQueue,
)


class QueueRunner:
    """Dequeues actions from a ``MemoryOrchestrationQueue`` and dispatches
    them to the engine until the queue is empty.

    Unlike ``OrchestrationWorker`` (which loops forever), this runner
    returns as soon as the queue drains — ideal for deterministic tests.
    """

    def __init__(self, engine: Any, queue: MemoryOrchestrationQueue) -> None:
        self._engine = engine
        self._queue = queue

    async def run_until_idle(self, max_actions: int = 1000) -> int:
        """Drain the queue. Returns the number of actions processed.

        ``max_actions`` guards against infinite loops (a misbehaving
        handler that keeps re-enqueueing itself).
        """
        processed = 0
        while not self._queue._queue.empty():
            if processed >= max_actions:
                raise RuntimeError(
                    f"run_until_idle exceeded {max_actions} actions; "
                    "possible infinite enqueue loop"
                )
            action = await self._queue.dequeue()
            await self._dispatch(action)
            processed += 1
        return processed

    async def _dispatch(self, action: Any) -> None:
        if isinstance(action, StartRunAction):
            await self._engine.orchestrate_start(action.run_id)
        elif isinstance(action, ContinueRunAction):
            await self._engine.orchestrate_continue(
                action.run_id, action.task_id,
                test_mode=action.test_mode,
            )
        elif isinstance(action, FailTaskAction):
            await self._engine.orchestrate_fail(action.run_id, action.task_id)
        elif isinstance(action, ResumeTaskAction):
            await self._engine.orchestrate_resume(
                action.run_id, action.task_id,
                test_mode=action.test_mode,
            )
        elif isinstance(action, ExecuteNodeAction):
            await self._engine.orchestrate_execute_node(
                action.run_id, action.task_id,
            )
        elif isinstance(action, LoopIterationAction):
            await self._engine.orchestrate_loop_iteration(
                action.run_id, action.task_id,
                action.loop_node_id, action.iteration_index,
            )
        elif isinstance(action, ResumeRunAction):
            await self._engine.orchestrate_resume_run(action.run_id)
        else:
            raise AssertionError(f"Unknown action type: {type(action).__name__}")


def attach_queue(engine: Any) -> tuple[MemoryOrchestrationQueue, QueueRunner]:
    """Wire a fresh ``MemoryOrchestrationQueue`` to ``engine`` and return
    both the queue and a ``QueueRunner`` bound to them.
    """
    queue = MemoryOrchestrationQueue()
    engine.set_queue(queue)
    return queue, QueueRunner(engine, queue)
