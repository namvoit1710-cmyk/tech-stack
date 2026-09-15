"""SA-2605 run guard — section membership enforcement (static + ⚡ dynamic), and
coexistence with the legacy SA-2450 / SA-2496 dynamic-env guard.

Proves the new model blocks an out-of-section environment AND that the old
per-flow-subset behaviour is preserved (fallback when a flow has no SA-2605
binding).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.layer2_application.features.start_run.use_cases.section_run_guard import (
    check_section_run,
)
from app.layer2_application.orchestration.child_workflow_coordinator import (
    ChildWorkflowCoordinator,
    WorkflowExecutionError,
)
from app.layer1_domain.entities.workflow_section import WorkflowSection
from app.layer1_domain.entities.section_membership import SectionMembership
from app.layer1_domain.entities.workflow_environment_assignment import (
    WorkflowEnvironmentAssignment,
)
from app.layer4_frameworks.persistence.memory.memory_workflow_section_repo import (
    MemoryWorkflowSectionRepo,
)
from app.layer4_frameworks.persistence.memory.memory_section_membership_repo import (
    MemorySectionMembershipRepo,
)
from app.layer4_frameworks.persistence.memory.memory_workflow_environment_repo import (
    MemoryWorkflowEnvironmentRepo,
)


def _bound_repos():
    ws = MemoryWorkflowSectionRepo()
    sm = MemorySectionMembershipRepo()
    ws.save(WorkflowSection.create(tenant_id="t1", workflow_id="wf1", section="MCK", created_by="f"))
    sm.save(SectionMembership.create(tenant_id="t1", section="MCK", environment_ids=["dev", "qas"], created_by="f"))
    return ws, sm


# ── check_section_run (start-run path) ────────────────────────────────
def test_section_guard_allows_in_section_blocks_out():
    ws, sm = _bound_repos()
    g = lambda env, wf="wf1": check_section_run(
        workflow_section_repo=ws, section_membership_repo=sm,
        tenant_id="t1", workflow_id=wf, environment_id=env,
    )
    assert g("") is None and g("__inherit__") is None      # empty / inherit ok
    assert g("dev") is None and g("qas") is None            # in section ok
    assert g("sandbox") and "MCK" in g("sandbox")           # out of section blocked
    assert g("dev", "unbound") is None                      # unbound wf → dormant
    assert check_section_run(                                 # repos missing → dormant
        workflow_section_repo=None, section_membership_repo=None,
        tenant_id="t1", workflow_id="wf1", environment_id="sandbox",
    ) is None


# ── real coordinator dynamic-env path (SA-2605 + SA-2450/2496 coexistence) ──
def _coordinator():
    ws, sm = _bound_repos()
    we = MemoryWorkflowEnvironmentRepo()
    # childA: SA-2605 section binding (MCK=[dev,qas]) via _bound_repos → rebind to childA
    ws.save(WorkflowSection.create(tenant_id="t1", workflow_id="childA", section="MCK", created_by="f"))
    # childLegacy: SA-2450 assignment MCK=[dev], NO SA-2605 binding
    we.save(WorkflowEnvironmentAssignment.create(
        tenant_id="t1", workflow_id="childLegacy", section="MCK", environment_ids=["dev"], created_by="f"))
    return ChildWorkflowCoordinator(
        engine=None, run_repo=None, task_repo=None, workflow_repo=None,
        workflow_snapshot_repo=None, data_store_service=None, variable_resolver=None,
        environment_repo=None,  # isolate the guard (skip env load)
        workflow_environment_repo=we, workflow_section_repo=ws, section_membership_repo=sm,
    )


def _bind(coord, wf, env, dyn=True):
    child = SimpleNamespace(tenant_id="t1", workflow_id=wf, context=SimpleNamespace(variables={}))
    parent = SimpleNamespace(tenant_id="t1", workflow_id="parent",
                             context=SimpleNamespace(variables={"env": {"KEY": "v"}}))
    coord._bind_child_environment(child, parent, env, is_dynamic=dyn)


def test_dynamic_env_sa2605_in_section_allowed():
    _bind(_coordinator(), "childA", "dev")   # no raise


def test_dynamic_env_sa2605_out_of_section_hard_blocked():
    with pytest.raises(WorkflowExecutionError) as ei:
        _bind(_coordinator(), "childA", "sandbox")
    assert "ENVIRONMENT_NOT_IN_SECTION" in str(ei.value)


def test_dynamic_env_legacy_sa2450_still_enforced():
    # feature cũ: a sub-flow with only an SA-2450 assignment keeps SA-2496 behaviour
    with pytest.raises(WorkflowExecutionError) as ei:
        _bind(_coordinator(), "childLegacy", "qas")   # qas ∉ subset [dev]
    assert "ENVIRONMENT_NOT_ASSIGNED" in str(ei.value)


def test_dynamic_env_legacy_sa2450_in_subset_allowed():
    _bind(_coordinator(), "childLegacy", "dev")   # dev ∈ subset → no raise


def test_dynamic_env_inherit_sentinel_unchanged():
    _bind(_coordinator(), "childA", "__inherit__")   # no raise, inherits parent $env
