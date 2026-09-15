"""On-demand task creation.

Phase 5.2.2 — ``create_task`` now inherits ``scope_stack`` from an
ambient contextvar (``_current_scope_stack_var``) set by the engine
before every ``invoke_handler`` call. This means successor tasks
created deep inside a loop body's handler chain automatically carry
the enclosing loop's scope, which is what lets the body-completion
hook in ``OrchestrationEngine.maybe_enqueue_loop_iteration`` attribute
completions to the correct iteration when running in queued mode.

Legacy path (no contextvar set) is unchanged — ``scope_stack``
stays ``None`` on the returned task.
"""

from __future__ import annotations

import contextvars
from typing import Any

from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects.loop_scope import LoopScope


# Phase 5.2.2 — ambient scope-stack for successor task inheritance.
# The engine sets this via ``set_current_scope_stack`` right before
# invoking a handler and resets it after. Any ``create_task`` call
# inside the handler's dispatch of successors reads this var and
# inherits the stack onto the new task.
_current_scope_stack_var: contextvars.ContextVar[list[LoopScope] | None] = (
    contextvars.ContextVar("current_scope_stack", default=None)
)


def set_current_scope_stack(
    stack: list[LoopScope] | None,
) -> contextvars.Token:
    """Set the ambient scope stack for the current async context.

    Returns a token that should be passed to ``reset_current_scope_stack``
    in a ``finally`` block so nested invocations don't leak scopes
    across each other.
    """
    return _current_scope_stack_var.set(stack)


def reset_current_scope_stack(token: contextvars.Token) -> None:
    """Restore the previous ambient scope stack using a token from
    ``set_current_scope_stack``."""
    _current_scope_stack_var.reset(token)


def get_current_scope_stack() -> list[LoopScope] | None:
    """Read the ambient scope stack. Primarily for tests; production
    code reads it implicitly via ``TaskFactory.create_task``."""
    return _current_scope_stack_var.get()


class TaskFactory:

    @staticmethod
    def task_id_for(run: WorkflowRun, node_id: str) -> str:
        """Visit-aware deterministic task id.

        Visit 0 keeps the legacy ``{run}-{node}`` shape; revisits append
        ``-v{n}`` so a node re-entered through a cycle gets a distinct
        task — and therefore distinct task-id-derived event ids — instead
        of silently upserting over its completed prior attempt. The
        counter lives on ``run.context.node_visits`` and is bumped by the
        dispatch paths when they fan out to an already-completed node;
        within one visit (dispatch → handler re-create → save) the counter
        is stable, so a handler's own ``create_task`` call resolves to the
        same id as the dispatched task (the intentional upsert).
        """
        visit = run.context.node_visits.get(node_id, 0)
        base = f"{run.id}-{node_id}"
        return base if visit <= 0 else f"{base}-v{visit}"

    @staticmethod
    def create_task(
        node: WorkflowNode,
        run: WorkflowRun,
        *,
        resolved_input: dict[str, Any] | None = None,
    ) -> Task:
        task_id = TaskFactory.task_id_for(run, node.id)
        # Inherit scope_stack from the ambient contextvar so successor
        # tasks built inside a body handler chain carry the enclosing
        # loop scope.
        inherited_scope = _current_scope_stack_var.get()
        task_scope_stack: list[LoopScope] | None = None
        if inherited_scope is not None:
            task_scope_stack = list(inherited_scope)
        return Task(
            id=task_id,
            run_id=run.id,
            node_id=node.id,
            status=TaskStatus.READY,
            worker_type=node.worker_type,
            resolved_input=resolved_input,
            scope_stack=task_scope_stack,
            idempotency_key=f"{task_id}-attempt-0",
            run_generation=run.generation,
        )
