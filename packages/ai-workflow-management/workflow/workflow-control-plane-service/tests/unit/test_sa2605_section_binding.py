"""SA-2605 — Section-only binding model layer: WorkflowSection (workflow→1 section)
+ SectionMembership (Section↔Environment many-to-many).

Covers: entity invariants, repo roundtrip, one-active-row uniqueness (CAS), rebind,
membership dedupe, the many-to-many reverse lookup (one env in many sections), and
clear_section (unbind a section's workflows).
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow_section import WorkflowSection
from app.layer1_domain.entities.section_membership import SectionMembership
from app.layer1_domain.exceptions import ValidationError
from app.layer2_application.repositories.workflow_section_repo_interface import CASMismatch
from app.layer4_frameworks.persistence.memory.memory_workflow_section_repo import (
    MemoryWorkflowSectionRepo,
)
from app.layer4_frameworks.persistence.memory.memory_section_membership_repo import (
    MemorySectionMembershipRepo,
)


# ── WorkflowSection entity ────────────────────────────────────────────
def test_workflow_section_requires_section_and_workflow():
    with pytest.raises(ValidationError):
        WorkflowSection.create(tenant_id="t1", workflow_id="wf1", section="", created_by="felix")
    with pytest.raises(ValidationError):
        WorkflowSection.create(tenant_id="t1", workflow_id="", section="MCK", created_by="felix")


def test_workflow_section_rebind_touches_version():
    w = WorkflowSection.create(tenant_id="t1", workflow_id="wf1", section="MCK", created_by="felix")
    assert w.version == 1
    w.set_section(section="CLS", updated_by="felix")
    assert w.section == "CLS" and w.version == 2


# ── WorkflowSection repo ──────────────────────────────────────────────
def test_workflow_section_repo_roundtrip_and_uniqueness():
    r = MemoryWorkflowSectionRepo()
    w = WorkflowSection.create(tenant_id="t1", workflow_id="wf1", section="MCK", created_by="felix")
    r.save(w)
    got = r.get_by_workflow(tenant_id="t1", workflow_id="wf1")
    assert got is not None and got.section == "MCK"
    # one ACTIVE binding per (tenant, workflow)
    with pytest.raises(CASMismatch):
        r.save(WorkflowSection.create(tenant_id="t1", workflow_id="wf1", section="CLS", created_by="felix"))
    # rebind the same aggregate with CAS
    got.set_section(section="S4", updated_by="felix")
    r.save(got, expected_version=1)
    assert r.get_by_workflow(tenant_id="t1", workflow_id="wf1").section == "S4"
    # list workflows bound to a section
    assert [x.workflow_id for x in r.list_by_section(tenant_id="t1", section="S4")] == ["wf1"]


def test_workflow_section_unassigned_is_none():
    r = MemoryWorkflowSectionRepo()
    assert r.get_by_workflow(tenant_id="t1", workflow_id="nope") is None


def test_clear_section_unbinds_all_its_workflows():
    r = MemoryWorkflowSectionRepo()
    for wf in ("a", "b", "c"):
        r.save(WorkflowSection.create(tenant_id="t1", workflow_id=wf, section="MCK", created_by="felix"))
    r.save(WorkflowSection.create(tenant_id="t1", workflow_id="d", section="CLS", created_by="felix"))
    n = r.clear_section(tenant_id="t1", section="MCK", updated_by="felix")
    assert n == 3
    assert r.get_by_workflow(tenant_id="t1", workflow_id="a") is None
    assert r.get_by_workflow(tenant_id="t1", workflow_id="d").section == "CLS"


# ── SectionMembership (many-to-many) ──────────────────────────────────
def test_membership_dedupes_environment_ids():
    m = SectionMembership.create(
        tenant_id="t1", section="MCK", environment_ids=["dev", "qas", "dev", ""], created_by="felix"
    )
    assert m.environment_ids == ["dev", "qas"]


def test_membership_many_to_many_reverse_lookup():
    repo = MemorySectionMembershipRepo()
    repo.save(SectionMembership.create(tenant_id="t1", section="MCK", environment_ids=["dev", "qas"], created_by="felix"))
    repo.save(SectionMembership.create(tenant_id="t1", section="CLS", environment_ids=["dev", "prod"], created_by="felix"))
    # dev belongs to BOTH sections; prod to only CLS
    assert repo.list_sections_for_environment(tenant_id="t1", environment_id="dev") == ["CLS", "MCK"]
    assert repo.list_sections_for_environment(tenant_id="t1", environment_id="prod") == ["CLS"]
    # one ACTIVE row per (tenant, section)
    with pytest.raises(CASMismatch):
        repo.save(SectionMembership.create(tenant_id="t1", section="MCK", environment_ids=["x"], created_by="felix"))


def test_membership_edit_replaces_set_with_cas():
    repo = MemorySectionMembershipRepo()
    repo.save(SectionMembership.create(tenant_id="t1", section="MCK", environment_ids=["dev", "qas"], created_by="felix"))
    m = repo.get_by_section(tenant_id="t1", section="MCK")
    m.set_environments(environment_ids=["dev", "uat"], updated_by="felix")
    repo.save(m, expected_version=1)
    got = repo.get_by_section(tenant_id="t1", section="MCK")
    assert got.environment_ids == ["dev", "uat"]
    assert "MCK" not in repo.list_sections_for_environment(tenant_id="t1", environment_id="qas")


def test_membership_list_all_sorted():
    repo = MemorySectionMembershipRepo()
    repo.save(SectionMembership.create(tenant_id="t1", section="S4", environment_ids=[], created_by="felix"))
    repo.save(SectionMembership.create(tenant_id="t1", section="CLS", environment_ids=["dev"], created_by="felix"))
    assert [m.section for m in repo.list_all(tenant_id="t1")] == ["CLS", "S4"]
