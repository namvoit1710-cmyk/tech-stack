"""Repo-driven projection rebuild — the application shell around the pure
``run_projection`` reducer.

``rebuild_run_state`` replays a run's event log (read through the
``IEventLogRepository`` port) into a single ``ProjectedRunState`` by folding
every envelope through ``ProjectedRunState.apply`` (the pure ``evolve``
reducer, which lives on the value object in
``app.layer1_domain.run_projection``). This module is the thin I/O shell that
drives it from a repository — hence layer2.
"""

from __future__ import annotations

from app.layer1_domain.run_projection import ProjectedRunState
from app.layer2_application.interfaces.event_log_repository_interface import (
    IEventLogRepository,
)


def rebuild_run_state(
    run_id: str,
    event_log_repo: IEventLogRepository,
) -> ProjectedRunState:
    """Replay a run's event log into a single ``ProjectedRunState``.

    Reads every envelope for ``run_id`` from ``event_log_repo`` in the
    order the log assigned and folds them through ``ProjectedRunState.apply``.
    Never mutates the log — the repository is consumed read-only.

    Returns ``ProjectedRunState(run_id=run_id)`` if the log contains no
    entries for the run; the caller can check ``state.status`` against
    its default (``"PENDING"``) to distinguish "no events" from "not
    started yet".
    """
    state = ProjectedRunState(run_id=run_id)
    for envelope in event_log_repo.list_by_run(run_id):
        state = state.apply(envelope)
    return state
