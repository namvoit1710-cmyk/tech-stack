"""Regression: a projected run that leaves a failed state must stop reporting
the failure.

``RunStatusChanged(status="failed")`` sets ``ProjectedRunState.error`` and
nothing ever unset it, so the string outlived the failure. ``GET
/api/v1/runs/{id}`` reads exactly this field (``GetRunStatusUseCase`` returns
``view.error``), which made a recovered run report itself as COMPLETED *and*
broken.

Measured on tenant-1 2026-08-25: four of eight runs of a four-way fan-out came
back ``status=COMPLETED`` carrying ``"Run 'x': expected version 7, found 8"``,
while ``GET /runs/{id}/nodes/status`` for the same run reported no error and
every branch had produced its output. The aggregate was right the whole time —
``OrchestrationEngine._finalize`` clears ``run.error`` when it completes a run,
with a comment saying it is for exactly this case. Only the projection kept it.

``check_run_completion`` deliberately allows a FAILED run to be re-evaluated
("a rerun or late completion may have resolved all failures"), so
failed -> completed is a supported transition, not an anomaly to be prevented.
"""

from __future__ import annotations

from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer2_application.projection import ProjectedRunState

_seq = 0


def _env(status: str, run_id: str = "run-1", **payload) -> EventLogEnvelope:
    global _seq
    _seq += 1
    return EventLogEnvelope(
        event_id=f"RunStatusChanged-{run_id}-{_seq}",
        run_id=run_id,
        topic="runstatuschanged",
        event_type="RunStatusChanged",
        timestamp="2026-08-25T00:00:00Z",
        payload={"status": status, "run_id": run_id, **payload},
    )


def _failed() -> ProjectedRunState:
    state = ProjectedRunState(run_id="run-1", status="RUNNING").apply(
        _env("failed", error="Run 'run-1': expected version 7, found 8"),
    )
    assert state.status == "FAILED"
    assert state.error == "Run 'run-1': expected version 7, found 8"
    return state


def test_completing_a_previously_failed_run_clears_the_error():
    state = _failed().apply(_env("completed", output_data={"ok": True}))
    assert state.status == "COMPLETED"
    assert not state.error, (
        f"a COMPLETED run still reports {state.error!r} — this is what "
        f"GET /api/v1/runs/{{id}} serves"
    )


def test_resuming_a_failed_run_clears_the_error():
    """``RerunTaskUseCase`` sets ``run.error = None`` on the aggregate and emits
    "resumed" specifically to move the projector off the terminal state. The
    projection has to follow it all the way."""
    state = _failed().apply(_env("resumed"))
    assert state.status == "RUNNING"
    assert not state.error


def test_starting_clears_a_stale_error():
    state = _failed().apply(_env("started", workflow_id="wf-1"))
    assert state.status == "RUNNING"
    assert not state.error


def test_a_failure_still_reports_itself():
    """The clearing must not swallow the case the field exists for."""
    state = _failed()
    assert state.status == "FAILED"
    assert "expected version 7" in state.error


def test_cancelling_keeps_the_recorded_reason():
    """CANCELLED is deliberately NOT cleared — a cancellation can record why,
    and no aggregate path clears it there."""
    state = _failed().apply(_env("cancelled"))
    assert state.status == "CANCELLED"
    assert state.error == "Run 'run-1': expected version 7, found 8"
