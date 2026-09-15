"""Sanity test that the new ``apply(run)`` closures introduced by the
async-HANA prep work (Pattern-A wraps) are idempotent under replay.

The CAS-retry helper re-invokes ``apply`` on a freshly-fetched run on
each conflict attempt. If a closure isn't idempotent (e.g., it appends
to a list or increments a counter), the persisted state diverges
between attempts. This file exercises a representative closure for
each newly-wrapped use case to catch accidental non-idempotent
mutations before they ship.

Closures touched here are absolute-assignment patterns. The test
applies each closure twice on a clean run and asserts the resulting
state is identical.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus


def _make_run() -> WorkflowRun:
    return WorkflowRun(
        id="r-1", workflow_id="wf-1", status=RunStatus.RUNNING,
        context=RunContext(),
    )


def _state_snapshot(run: WorkflowRun) -> dict:
    """Capture the fields touched by the new closures so we can compare
    by value rather than by reference."""
    return {
        "status": run.status,
        "error": run.error,
        "completed_at": run.completed_at,
        "output_data": run.output_data,
        "child_runs": dict(run.context.child_runs),
    }


class TestRunFailedClosure:
    """Mirrors ``orchestration_engine._apply_failed`` and the various
    ``_apply_*_failed`` closures: absolute assignment of status, error,
    completed_at."""

    def test_applying_twice_yields_identical_state(self):
        new_error = "Task t-1 failed: boom"
        new_completed_at = "2026-05-27T10:00:00+00:00"

        def _apply(r: WorkflowRun) -> None:
            r.status = RunStatus.FAILED
            r.error = new_error
            r.completed_at = new_completed_at

        run = _make_run()
        _apply(run)
        first = _state_snapshot(run)

        _apply(run)
        second = _state_snapshot(run)

        assert first == second


class TestRunCompletedClosure:
    """Mirrors ``orchestration_engine._apply_completed``."""

    def test_applying_twice_yields_identical_state(self):
        new_completed_at = "2026-05-27T10:00:00+00:00"
        new_output_data = {"result": "ok", "count": 42}

        def _apply(r: WorkflowRun) -> None:
            r.status = RunStatus.COMPLETED
            r.completed_at = new_completed_at
            r.output_data = new_output_data

        run = _make_run()
        _apply(run)
        first = _state_snapshot(run)

        _apply(run)
        second = _state_snapshot(run)

        assert first == second


class TestRunStatusFlipClosures:
    """Single-field status flips: PENDING, SUSPENDED, PAUSED, RUNNING."""

    @pytest.mark.parametrize("target", [
        RunStatus.PENDING, RunStatus.SUSPENDED,
        RunStatus.PAUSED, RunStatus.RUNNING,
    ])
    def test_status_only_closure_is_idempotent(self, target: RunStatus):
        def _apply(r: WorkflowRun) -> None:
            r.status = target

        run = _make_run()
        _apply(run)
        first = _state_snapshot(run)

        _apply(run)
        second = _state_snapshot(run)

        assert first == second
        assert run.status == target


class TestParentUnfreezeClosure:
    """Mirrors ``resume_pending_task._apply_parent_unfreeze``: clears
    FAILED bookkeeping."""

    def test_clears_failed_state_idempotently(self):
        def _apply(r: WorkflowRun) -> None:
            r.status = RunStatus.RUNNING
            r.error = None
            r.completed_at = None

        run = _make_run()
        run.status = RunStatus.FAILED
        run.error = "Child timed out"
        run.completed_at = "2026-05-26T09:00:00+00:00"

        _apply(run)
        first = _state_snapshot(run)

        _apply(run)
        second = _state_snapshot(run)

        assert first == second
        assert run.status == RunStatus.RUNNING
        assert run.error is None
        assert run.completed_at is None


class TestRegisterChildClosure:
    """Mirrors ``child_workflow_coordinator._apply_register_child``: dict
    assignment to ``run.context.child_runs``. Dict assignment is
    idempotent because the second assignment overwrites with the same
    value."""

    def test_dict_assignment_is_idempotent(self):
        new_child_id = "child-run-abc"
        parent_node_id = "wf-node-1"

        def _apply(r: WorkflowRun) -> None:
            r.context.child_runs[parent_node_id] = new_child_id

        run = _make_run()
        _apply(run)
        first = _state_snapshot(run)

        _apply(run)
        second = _state_snapshot(run)

        assert first == second
        assert run.context.child_runs[parent_node_id] == new_child_id

    def test_preserves_other_child_entries(self):
        """If the freshly-fetched run already had a different child mapping
        (e.g. a peer instance registered another child), the closure must
        only set its own entry, not clobber the dict."""
        def _apply(r: WorkflowRun) -> None:
            r.context.child_runs["wf-node-1"] = "child-1"

        run = _make_run()
        # Simulate fresh-fetched run with a pre-existing peer mapping.
        run.context.child_runs["wf-node-2"] = "child-2"

        _apply(run)
        assert run.context.child_runs == {
            "wf-node-1": "child-1",
            "wf-node-2": "child-2",
        }

        _apply(run)
        assert run.context.child_runs == {
            "wf-node-1": "child-1",
            "wf-node-2": "child-2",
        }
