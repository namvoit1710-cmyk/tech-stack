"""Test doubles for ``IRunContextViewStore``.

Phase 3b.3 deleted ``LiveRunContextViewStore`` from production — the
projector-backed store is the only authoritative read path now. Test
fixtures that populate a ``MemoryRunRepository`` directly and need
a matching view store without spinning up the event log + projector
use ``LiveViewStoreDouble`` from this module. Production code must
not import it.
"""
from __future__ import annotations

from typing import Any

from app.layer1_domain.entities.run_context_snapshot import RunContextSnapshot
from app.layer1_domain.entities.workflow_run import WorkflowRun


class LiveViewStoreDouble:
    """Minimal ``IRunContextViewStore`` that reads directly from a run
    repository — mirrors the old ``LiveRunContextViewStore`` shape for
    tests that pre-populate aggregates and want to observe the same
    state through a view interface.
    """

    def __init__(self, run_repo: Any) -> None:
        self._run_repo = run_repo

    def get(self, run_id: str) -> RunContextSnapshot | None:
        run = self._run_repo.get_by_id(run_id)
        if run is None:
            return None
        return RunContextSnapshot.from_run(run)

    def get_by_root_id(self, root_id: str) -> list[RunContextSnapshot]:
        runs: list[WorkflowRun] = self._run_repo.get_by_root_id(root_id)
        return [RunContextSnapshot.from_run(r) for r in runs]
