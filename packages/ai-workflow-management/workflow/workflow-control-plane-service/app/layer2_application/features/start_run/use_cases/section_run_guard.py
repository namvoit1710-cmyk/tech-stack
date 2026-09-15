"""Run guard for the section-only binding (SA-2605), superseding the SA-2450
per-flow env-subset guard.

A run's ``environment_id`` must be a MEMBER of the section the workflow is bound to
(``AIW_SECTION_ENVIRONMENT``). This covers both a statically-picked environment and
a ⚡ dynamically-resolved one (SA-2496) — the child-workflow coordinator calls the
same guard for the env it resolves at run time, so a dynamic env outside the
sub-flow's section is hard-blocked (no silent fallback).

Coexistence with SA-2450: the guard is DORMANT when the workflow has no SA-2605
section binding (returns None), so legacy AIW_WORKFLOW_ENVIRONMENT-bound and
unbound workflows are unaffected. It only enforces once a workflow is bound via the
new model.
"""
from __future__ import annotations

from typing import Any

# Subflow env-binding sentinel (SA-2458). "inherit the parent run's $env" — never
# an id to validate.
_INHERIT_ENVIRONMENT = "__inherit__"


def check_section_run(
    *,
    workflow_section_repo: Any,
    section_membership_repo: Any,
    tenant_id: str,
    workflow_id: str,
    environment_id: str,
) -> str | None:
    """Return an error message if the environment is not in the bound section,
    else None.

    - empty / ``__inherit__`` environment_id → allowed.
    - repos unavailable → allowed (feature dormant).
    - workflow not bound to a section (SA-2605) → allowed (dormant; SA-2450 guard
      or "no environment" semantics apply).
    - bound → the id must be a member of that section's environments.
    """
    if not environment_id or environment_id == _INHERIT_ENVIRONMENT:
        return None
    if workflow_section_repo is None or section_membership_repo is None:
        return None
    try:
        binding = workflow_section_repo.get_by_workflow(
            tenant_id=tenant_id, workflow_id=workflow_id
        )
    except Exception:
        return None
    if binding is None or not (binding.section or "").strip():
        return None  # not bound via SA-2605 — dormant
    try:
        membership = section_membership_repo.get_by_section(
            tenant_id=tenant_id, section=binding.section
        )
    except Exception:
        return None
    members = membership.environment_ids if membership is not None else []
    if environment_id not in members:
        return (
            f"The selected environment is not in the workflow's section "
            f"'{binding.section}'."
        )
    return None
