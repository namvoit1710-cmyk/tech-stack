"""Pure unit tests for ``WorkflowRun.decide_completion``.

This is the functional core of run completion (the ``decide`` half of the
decide/evolve seam). It takes a list of fake tasks plus the projector-view
skip set and returns a ``CompletionDecision`` — no repos, no publishers, no
async, no mocks. The orchestration-engine integration of this decision (the
I/O shell) is covered by ``TestCheckRunCompletion`` in
``test_orchestration_engine.py``.
"""

from __future__ import annotations

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow_run import (
    WorkflowRun,
    RunContext,
    CompletionDecision,
)
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus


def _run(status: RunStatus = RunStatus.RUNNING, **ctx) -> WorkflowRun:
    return WorkflowRun(
        id="run-1", workflow_id="wf-1", status=status,
        context=RunContext(**ctx),
    )


def _task(node_id: str, status: TaskStatus, **kw) -> Task:
    return Task(id=f"t-{node_id}", run_id="run-1", node_id=node_id, status=status, **kw)


# ── terminal short-circuit ──────────────────────────────────────────────

def test_completed_run_decides_none():
    run = _run(status=RunStatus.COMPLETED)
    decision = run.decide_completion(
        [_task("a", TaskStatus.RUNNING)], view_skipped=frozenset(),
    )
    assert decision == CompletionDecision("none")


def test_cancelled_run_decides_none():
    run = _run(status=RunStatus.CANCELLED)
    decision = run.decide_completion(
        [_task("a", TaskStatus.FAILED)], view_skipped=frozenset(),
    )
    assert decision.kind == "none"


# ── failed ──────────────────────────────────────────────────────────────

def test_failed_task_decides_failed_and_names_task():
    run = _run()
    decision = run.decide_completion(
        [_task("a", TaskStatus.COMPLETED), _task("b", TaskStatus.FAILED)],
        view_skipped=frozenset(),
    )
    assert decision.kind == "failed"
    assert decision.failed_task_id == "t-b"


def test_failed_task_with_skipped_marker_is_not_a_failure():
    run = _run()
    decision = run.decide_completion(
        [_task("a", TaskStatus.FAILED, output_metadata={"skipped": True})],
        view_skipped=frozenset(),
    )
    assert decision.kind == "completed"


def test_failed_run_recovers_to_completed_when_failures_resolved():
    # A FAILED run is re-evaluated (not terminal): only completed tasks left
    # -> it recovers to COMPLETED.
    run = _run(status=RunStatus.FAILED)
    decision = run.decide_completion(
        [_task("a", TaskStatus.COMPLETED)], view_skipped=frozenset(),
    )
    assert decision.kind == "completed"


# ── completed (quiescence) ──────────────────────────────────────────────

def test_all_completed_decides_completed():
    run = _run()
    decision = run.decide_completion(
        [_task("a", TaskStatus.COMPLETED), _task("b", TaskStatus.COMPLETED)],
        view_skipped=frozenset(),
    )
    assert decision == CompletionDecision("completed")


def test_no_tasks_decides_completed():
    run = _run()
    assert run.decide_completion([], view_skipped=frozenset()).kind == "completed"


# ── still active ────────────────────────────────────────────────────────

def test_running_task_decides_none():
    run = _run()
    decision = run.decide_completion(
        [_task("a", TaskStatus.RUNNING)], view_skipped=frozenset(),
    )
    assert decision.kind == "none"


def test_mixed_pending_and_running_decides_none():
    # Not all active tasks are PENDING -> not a PENDING transition, and there
    # is live work -> "none".
    run = _run()
    decision = run.decide_completion(
        [_task("a", TaskStatus.PENDING), _task("b", TaskStatus.RUNNING)],
        view_skipped=frozenset(),
    )
    assert decision.kind == "none"


# ── pending ─────────────────────────────────────────────────────────────

def test_all_pending_while_running_decides_pending():
    run = _run(status=RunStatus.RUNNING)
    decision = run.decide_completion(
        [_task("a", TaskStatus.PENDING), _task("b", TaskStatus.PENDING)],
        view_skipped=frozenset(),
    )
    assert decision.kind == "pending"


def test_all_pending_but_not_running_decides_none():
    # An already-PENDING run does not re-transition to PENDING.
    run = _run(status=RunStatus.PENDING)
    decision = run.decide_completion(
        [_task("a", TaskStatus.PENDING)], view_skipped=frozenset(),
    )
    assert decision.kind == "none"


# ── skip filtering ──────────────────────────────────────────────────────

def test_skipped_node_active_task_does_not_block_completion():
    # A skipped branch leaf with a leftover READY task must not keep the run
    # from quiescing.
    run = _run(skipped_nodes={"skipme"})
    decision = run.decide_completion(
        [_task("a", TaskStatus.COMPLETED), _task("skipme", TaskStatus.READY)],
        view_skipped=frozenset({"skipme"}),
    )
    assert decision.kind == "completed"


def test_skipped_node_failed_task_does_not_fail_run():
    run = _run(skipped_nodes={"skipme"})
    decision = run.decide_completion(
        [_task("a", TaskStatus.COMPLETED), _task("skipme", TaskStatus.FAILED)],
        view_skipped=frozenset({"skipme"}),
    )
    assert decision.kind == "completed"


def test_revisit_unskip_exception_makes_node_active_again():
    # SA-846: the node is still flagged skipped in the projector view, but a
    # revisit cleared it from the AGGREGATE skip set and bumped its visit
    # counter -> its fresh active task must count. The run is NOT quiescent.
    run = _run(skipped_nodes=set(), node_visits={"revisited": 1})
    decision = run.decide_completion(
        [_task("a", TaskStatus.COMPLETED), _task("revisited", TaskStatus.RUNNING)],
        view_skipped=frozenset({"revisited"}),  # view still lags
    )
    assert decision.kind == "none"


def test_view_skipped_without_visit_bump_stays_skipped():
    # Still in the aggregate skip set (no un-skip) -> the view skip stands and
    # the task is filtered out, so the run quiesces.
    run = _run(skipped_nodes={"revisited"}, node_visits={"revisited": 1})
    decision = run.decide_completion(
        [_task("a", TaskStatus.COMPLETED), _task("revisited", TaskStatus.RUNNING)],
        view_skipped=frozenset({"revisited"}),
    )
    assert decision.kind == "completed"
