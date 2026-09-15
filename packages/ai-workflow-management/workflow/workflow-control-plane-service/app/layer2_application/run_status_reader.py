"""Authoritative run-status reader.

Status transitions flow through the projector: a use case emits
``RunStatusChanged`` and the view store folds it out of the event
log. The event log is the authority. Read status through this
helper — never off ``run.status`` — because the aggregate can lag
or diverge: it is written by the transition itself, so an emit that
lands without its aggregate save (or the reverse) leaves the column
behind. Measured on tenant-1 2026-08-13: 4 runs carried a
``completed_at`` on the row with no terminal event in the log at
all.

The aggregate's STATUS column is not dead, though — do not stop
writing it. It is the sole index behind ``GET /api/v1/runs``, which
is SQL over the runs table (SELECT STATUS, ``STATUS IN (...)``,
ORDER BY STARTED_AT) and cannot replay per-run event logs. Every
transition keeps it current: started / completed / failed / paused /
resumed / suspended / pending, and — since the fix that restored
them — cancelled and sweep-timeout too. Phase 3b.2c had removed the
last two, which is precisely why cancelled runs read RUNNING in the
list and ``?status=CANCELLED`` matched nothing tenant-wide.

``read_authoritative_status`` returns the view store's current
status when available, falling back to the aggregate for runs with
no projected events (e.g. a freshly-created run before its first
``RunStarted`` is projected, or tests that construct
``WorkflowRun`` directly without emitting events).
"""
from __future__ import annotations

from typing import Optional

from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)


def read_authoritative_status(
    run: WorkflowRun,
    view_store: Optional[IRunContextViewStore],
) -> str:
    """Return the authoritative run status as an unwrapped string.

    Mirrors ``IRunContextView.status`` — callers compare against
    ``RunStatus.*.value`` rather than the enum.
    """
    if view_store is not None:
        view = view_store.get(run.id)
        if view is not None:
            return view.status
    status = run.status
    return status.value if hasattr(status, "value") else str(status)
