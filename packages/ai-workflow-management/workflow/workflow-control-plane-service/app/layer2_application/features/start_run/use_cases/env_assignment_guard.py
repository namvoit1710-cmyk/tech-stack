"""Run guard for the workflow↔section binding (SA-2450 W6/W7/W8).

A run's ``environment_id`` must be one the workflow is assigned to. An unassigned
workflow (no binding) rejects ANY concrete environment_id — its selector is empty
(hard scope). Empty (no environment) and the subflow ``__inherit__`` sentinel are
always allowed. Shared by StartRun and TestFullWorkflow so the guard fires on
every start path (manual, /test/full, trigger, schedule — the latter two funnel
through StartRun).
"""
from __future__ import annotations

from typing import Any

# Subflow env-binding sentinel (SA-2458 child_workflow_coordinator). A child node
# may carry this instead of a concrete id; it means "inherit the parent run's
# $env" and must never be treated as an environment id to validate.
_INHERIT_ENVIRONMENT = "__inherit__"


def check_environment_assignment(
    *,
    repo: Any,
    tenant_id: str,
    workflow_id: str,
    environment_id: str,
) -> str | None:
    """Return an error message if the environment is not assigned, else None.

    - empty / ``__inherit__`` ``environment_id`` → allowed (no env / inherit).
    - repo unavailable → allowed (feature dormant; never blocks a run).
    - workflow unassigned (no binding or empty subset) → any concrete
      environment_id is rejected.
    - assigned → the id must be in the assigned subset.
    """
    if not environment_id or environment_id == _INHERIT_ENVIRONMENT:
        return None
    if repo is None:
        return None
    try:
        assignment = repo.get_by_workflow(tenant_id=tenant_id, workflow_id=workflow_id)
    except Exception:
        # A lookup failure must not wedge a run; treat as dormant.
        return None
    if assignment is None or not assignment.environment_ids:
        return (
            "This workflow has no environment assigned — assign a section in "
            "Environments → Assign before selecting an environment to run."
        )
    if environment_id not in assignment.environment_ids:
        return "The selected environment is not assigned to this workflow."
    return None
