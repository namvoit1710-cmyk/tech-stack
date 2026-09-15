"""Orchestration queue protocol and action dataclasses.

Use cases enqueue actions; a background worker dequeues and processes them
via the OrchestrationEngine.  This decouples HTTP request handling from
the potentially long-running DAG traversal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Action dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OrchestrationAction:
    """Base action — every action carries a run_id."""
    run_id: str
    action_type: str = ""
    # Wall clock at publish, stamped by BrokerOrchestrationQueue. Producer and
    # consumer are the SAME process, so this is one clock and the difference at
    # dequeue is the broker hop end to end: buffered send, HTTPS POST, broker
    # fan-out, AMQP delivery. 0.0 means "not stamped" — an in-memory queue, a
    # test, or a message published before this field existed.
    published_at: float = 0.0


@dataclass
class StartRunAction(OrchestrationAction):
    """Dispatched after a run is created — processes entry nodes."""
    action_type: str = "start"


@dataclass
class ContinueRunAction(OrchestrationAction):
    """Dispatched after a task completes — processes successors."""
    action_type: str = "continue"
    task_id: str = ""
    test_mode: str | None = None


@dataclass
class FailTaskAction(OrchestrationAction):
    """Dispatched after a task fails — routes via failure edges or fails the run."""
    action_type: str = "fail"
    task_id: str = ""


@dataclass
class ResumeTaskAction(OrchestrationAction):
    """Dispatched after a pending task is resumed — processes successors."""
    action_type: str = "resume"
    task_id: str = ""
    test_mode: str | None = None


@dataclass
class ExecuteNodeAction(OrchestrationAction):
    """Dispatched to execute a single node via the unified queue-based pipeline.

    Replaces recursive node-dispatch calls.  Each node
    execution becomes a discrete action: the handler runs, state is
    applied, and successor tasks are enqueued as new ``ExecuteNodeAction``
    messages — no recursion.
    """
    action_type: str = "execute_node"
    task_id: str = ""


@dataclass
class LoopIterationAction(OrchestrationAction):
    """Dispatched when a loop body completes to feed the result back to the
    loop handler, which decides whether to emit the next iteration or the
    exit tasks.
    """
    action_type: str = "loop_iteration"
    task_id: str = ""
    loop_node_id: str = ""
    iteration_index: int = 0


@dataclass
class ResumeRunAction(OrchestrationAction):
    """Dispatched when a paused run is resumed — re-dispatches stalled tasks."""
    action_type: str = "resume_run"


@dataclass
class CheckRunCompletionAction(OrchestrationAction):
    """Dispatched when a node reaches a terminal state under
    ``EXECUTOR_MODE=executor`` so the run's quiescence is re-evaluated.

    In executor mode the engine bows out of handler invocation and the
    ``RunCompletionConsumer`` enqueues this action on every
    ``NodeCompleted`` / ``NodeFailed``. The worker routes it to
    ``OrchestrationEngine.orchestrate_check_completion`` — a
    completion-ONLY path (no fan-out; the DispatcherConsumer owns
    fan-out). ``check_run_completion`` is idempotent and quiescence-
    gated, so a check that fires before the run is done is a safe no-op.
    """
    action_type: str = "check_completion"


@dataclass
class EditNodeDataAction(OrchestrationAction):
    """Phase 3c: PATCH ``/runs/{id}/nodes/{node_id}/data`` re-routed
    through the orchestration queue so concurrent PATCHes for the
    same run serialise at the single-consumer worker instead of
    acquiring an in-process ``asyncio.Lock``.

    ``command`` is the full ``EditNodeDataCommand`` — kept as an opaque
    payload here (the queue interface deliberately doesn't import from
    ``features/``). ``correlation_id`` keys a future on the controller
    side so the HTTP request can await the worker's ``EditNodeDataResult``.

    Phase M.3.3: ``origin_instance_id`` tags which instance enqueued
    the action. When Kafka partition-by-run_id routes the action to a
    different instance than the one holding the HTTP caller's future,
    the processing worker publishes the result to the origin's
    response topic (``orchestration.responses.{origin_instance_id}``)
    rather than trying to resolve a local future it never allocated.
    Empty string means "resolve locally" (single-instance fast path).
    """
    action_type: str = "edit_node_data"
    correlation_id: str = ""
    command: Any = None
    origin_instance_id: str = ""


# ---------------------------------------------------------------------------
# Queue protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class IOrchestrationQueue(Protocol):
    async def enqueue(self, action: OrchestrationAction) -> None:
        """Put an action on the queue."""
        ...

    def enqueue_nowait(self, action: OrchestrationAction) -> None:
        """Synchronously put an action on the queue.

        For callers that run inside a synchronous publisher-tee ``_feed``
        (e.g. ``RunCompletionConsumer``) and cannot ``await``. Both
        backing implementations enqueue without blocking — the memory
        queue is unbounded and the broker publish is synchronous.
        """
        ...

    async def dequeue(self) -> OrchestrationAction:
        """Block until an action is available, then return it."""
        ...
