"""Unit tests for the Phase 5.2.2 ``scope_stack`` contextvar
propagation in ``TaskFactory.create_task``.

When the engine is about to invoke a handler, it sets
``_current_scope_stack_var`` to the currently-dispatched task's
``scope_stack`` (via ``invoke_handler``'s ``current_scope_stack``
kwarg). Inside that handler, any successor tasks built via
``task_factory.create_task`` automatically inherit the stack — so a
handler deep inside a loop body chain (e.g., the body-entry's
CONDITION successor) produces tasks that carry the enclosing
``LoopScope``. That's what lets the engine's
``maybe_enqueue_loop_iteration`` hook attribute completions to the
right iteration when running in queued mode.

Default behaviour (contextvar not set) is byte-identical to the
pre-5.2.2 ``create_task`` — the returned task has ``scope_stack=None``.
"""

from __future__ import annotations

import contextvars

from app.layer1_domain.entities.workflow import WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext
from app.layer2_application.orchestration.task_factory import (
    TaskFactory,
    get_current_scope_stack,
    reset_current_scope_stack,
    set_current_scope_stack,
)
from app.layer1_domain.value_objects.loop_scope import LoopScope
from app.layer1_domain.value_objects.node_type import NodeType


def _node(node_id: str = "n1") -> WorkflowNode:
    return WorkflowNode(id=node_id, node_type=NodeType.TASK, worker_type="w")


def _run() -> WorkflowRun:
    return WorkflowRun(id="run-1", workflow_id="wf", context=RunContext())


def test_no_contextvar_set_yields_none_scope():
    """Baseline: when no ambient contextvar is set,
    ``TaskFactory.create_task`` produces a task with
    ``scope_stack=None`` — identical to pre-Phase-5.2.2 behaviour."""
    assert get_current_scope_stack() is None
    task = TaskFactory.create_task(_node(), _run())
    assert task.scope_stack is None


def test_contextvar_set_inherits_stack():
    """When the engine sets the contextvar (e.g., around a handler
    call), successor tasks created inside the handler inherit the
    stack."""
    scope = LoopScope(
        loop_node_id="loop-1", invocation_id="inv-1", iter=2, total=5,
    )
    token = set_current_scope_stack([scope])
    try:
        task = TaskFactory.create_task(_node(), _run())
        assert task.scope_stack is not None
        assert len(task.scope_stack) == 1
        assert task.scope_stack[0].loop_node_id == "loop-1"
        assert task.scope_stack[0].invocation_id == "inv-1"
        assert task.scope_stack[0].iter == 2
    finally:
        reset_current_scope_stack(token)

    # After reset, subsequent create_task calls are back to None.
    assert get_current_scope_stack() is None
    task_after = TaskFactory.create_task(_node("n2"), _run())
    assert task_after.scope_stack is None


def test_nested_scope_stacks_inherit_full_chain():
    """Nested loops: engine sets contextvar with two LoopScope
    entries (outer + inner). A successor task inherits both."""
    outer = LoopScope(
        loop_node_id="loop-outer", invocation_id="OUTER", iter=1, total=2,
    )
    inner = LoopScope(
        loop_node_id="loop-inner", invocation_id="INNER", iter=0, total=3,
    )
    token = set_current_scope_stack([outer, inner])
    try:
        task = TaskFactory.create_task(_node(), _run())
        assert task.scope_stack is not None
        assert len(task.scope_stack) == 2
        assert task.scope_stack[0].loop_node_id == "loop-outer"
        assert task.scope_stack[1].loop_node_id == "loop-inner"
    finally:
        reset_current_scope_stack(token)


def test_inherited_stack_is_a_copy_not_a_reference():
    """Modifying the returned task's scope_stack must not affect the
    ambient contextvar (or vice versa) — the task should get a new
    list."""
    original = [LoopScope("loop-1", "inv-1", 0, 3)]
    token = set_current_scope_stack(original)
    try:
        task = TaskFactory.create_task(_node(), _run())
        assert task.scope_stack == original
        # Mutate the task's list.
        task.scope_stack.append(LoopScope("loop-2", "inv-2", 0, 5))
        # Contextvar value must be unchanged.
        current = get_current_scope_stack()
        assert current == original
        assert len(current) == 1
    finally:
        reset_current_scope_stack(token)


def test_stamps_run_generation_from_run():
    """G-guard: every task built via ``TaskFactory.create_task``
    carries the run's ``generation`` at the time of creation. Reset
    flows bump ``run.generation`` so later callbacks from a prior
    execution can be identified as stale."""
    run = _run()
    run.generation = 7
    task = TaskFactory.create_task(_node(), run)
    assert task.run_generation == 7


def test_stamps_default_generation_zero():
    """First-ever execution: both the run and its tasks run at
    generation 0. The guard check is a strict ``<`` so zero passes."""
    run = _run()
    assert run.generation == 0
    task = TaskFactory.create_task(_node(), run)
    assert task.run_generation == 0


def test_contextvar_resets_cleanly_between_async_contexts():
    """Each contextvars.copy_context() gives an isolated view — the
    contextvar doesn't leak between parallel tasks. This tests that
    invariant with the Task.factory API."""
    scope_A = LoopScope("loop-A", "invA", 0, 3)
    scope_B = LoopScope("loop-B", "invB", 0, 3)

    def ctx_a():
        token = set_current_scope_stack([scope_A])
        try:
            task = TaskFactory.create_task(_node("a"), _run())
            return task.scope_stack
        finally:
            reset_current_scope_stack(token)

    def ctx_b():
        token = set_current_scope_stack([scope_B])
        try:
            task = TaskFactory.create_task(_node("b"), _run())
            return task.scope_stack
        finally:
            reset_current_scope_stack(token)

    result_a = contextvars.copy_context().run(ctx_a)
    result_b = contextvars.copy_context().run(ctx_b)
    assert result_a[0].loop_node_id == "loop-A"
    assert result_b[0].loop_node_id == "loop-B"
    # Parent context was never touched.
    assert get_current_scope_stack() is None
