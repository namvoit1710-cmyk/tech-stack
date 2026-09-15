"""Resolve a run's frozen workflow definition.

Every read of a workflow during run execution — engine call sites,
write-path use cases, query-side projectors — goes through this helper
so the same immutable definition is observed everywhere.

Hard-fail policy: a run without a resolvable snapshot raises
``WorkflowSnapshotMissingError`` rather than silently degrading to the
live ``workflow_repo``. Pre-snapshot runs must be drained before the
phase-C/D rollout.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.exceptions import WorkflowSnapshotMissingError

if TYPE_CHECKING:  # pragma: no cover
    from app.layer1_domain.entities.workflow_run import WorkflowRun
    from app.layer2_application.repositories.workflow_snapshot_repository_interface import (
        IWorkflowSnapshotRepository,
    )


def load_workflow_for_run(
    run: "WorkflowRun",
    snapshot_repo: "IWorkflowSnapshotRepository",
) -> Workflow:
    """Return the snapshotted ``Workflow`` for ``run``.

    Raises ``WorkflowSnapshotMissingError`` when ``run`` has no
    ``workflow_snapshot_id`` or the referenced snapshot row is missing.
    """
    snapshot_id = run.workflow_snapshot_id
    if not snapshot_id:
        raise WorkflowSnapshotMissingError(run.id)
    snapshot = snapshot_repo.get_by_id(snapshot_id)
    if snapshot is None:
        raise WorkflowSnapshotMissingError(run.id, snapshot_id=snapshot_id)
    return Workflow.from_snapshot_dict(snapshot.definition)


def capture_snapshot(
    workflow: Workflow,
    snapshot_repo: "IWorkflowSnapshotRepository",
) -> str:
    """Persist a fresh snapshot of ``workflow`` and return its id.

    Callers (``StartRunUseCase``, ``TestFullWorkflowUseCase``, engine
    ``_start_child_workflow``) write this id onto the new run before
    saving so the engine has a resolvable definition from the first
    callback onward.
    """
    from app.layer1_domain.entities.workflow_snapshot import WorkflowSnapshot

    snapshot = WorkflowSnapshot(
        workflow_id=workflow.id,
        workflow_version=workflow.version,
        definition=workflow.to_snapshot_dict(),
    )
    snapshot_repo.save(snapshot)
    return snapshot.id
