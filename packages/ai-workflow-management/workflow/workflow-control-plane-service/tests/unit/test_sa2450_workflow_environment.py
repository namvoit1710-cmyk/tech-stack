"""SA-2450 — Workflow↔section binding: entity, repo, use cases, run guard.

Covers: one-section/≥1 invariant, replace-set, cross-section rejection,
unassigned=empty, reverse lookup, persistence natural key, and the run guard
(reject wrong/unassigned; allow assigned/empty/inherit).
"""
from __future__ import annotations

import asyncio

import pytest

from app.layer1_domain.entities.environment import Environment
from app.layer1_domain.entities.workflow_environment_assignment import (
    WorkflowEnvironmentAssignment,
)
from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.env_variable import EnvVariable
from app.layer2_application.errors import ErrorCode
from app.layer2_application.repositories.workflow_environment_repo_interface import (
    CASMismatch,
)
from app.layer4_frameworks.persistence.memory.memory_environment_repo import (
    MemoryEnvironmentRepo,
)
from app.layer4_frameworks.persistence.memory.memory_workflow_environment_repo import (
    MemoryWorkflowEnvironmentRepo,
)
from app.layer2_application.features.set_workflow_environment_assignment.use_cases.set_workflow_environment_assignment_use_case import (
    SetWorkflowEnvironmentAssignmentCommand,
    SetWorkflowEnvironmentAssignmentUseCase,
)
from app.layer2_application.features.get_workflow_environment_assignment.use_cases.get_workflow_environment_assignment_use_case import (
    GetWorkflowEnvironmentAssignmentCommand,
    GetWorkflowEnvironmentAssignmentUseCase,
)
from app.layer2_application.features.list_workflows_for_environment.use_cases.list_workflows_for_environment_use_case import (
    ListWorkflowsForEnvironmentCommand,
    ListWorkflowsForEnvironmentUseCase,
)
from app.layer2_application.features.start_run.use_cases.env_assignment_guard import (
    check_environment_assignment,
)


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _env(repo, section, name, tenant="t1"):
    e = Environment.create(
        tenant_id=tenant, section=section, name=name, created_by="u",
        variables=[EnvVariable.make(key="base_url", type="text", value="http://x")],
    )
    repo.save(e)
    return e.id


# ── entity invariants ────────────────────────────────────────────────
def test_entity_requires_section_and_at_least_one_env():
    with pytest.raises(ValidationError):
        WorkflowEnvironmentAssignment.create(
            tenant_id="t1", workflow_id="w1", section="", environment_ids=["e1"], created_by="u")
    with pytest.raises(ValidationError):
        WorkflowEnvironmentAssignment.create(
            tenant_id="t1", workflow_id="w1", section="S", environment_ids=[], created_by="u")


def test_entity_dedupes_and_touches():
    a = WorkflowEnvironmentAssignment.create(
        tenant_id="t1", workflow_id="w1", section="S", environment_ids=["e1", "e1", "e2"], created_by="u")
    assert a.environment_ids == ["e1", "e2"]
    v = a.version
    a.set_binding(section="S", environment_ids=["e2"], updated_by="u2")
    assert a.environment_ids == ["e2"] and a.version == v + 1


def _uc(env_repo, wf_repo):
    return SetWorkflowEnvironmentAssignmentUseCase(_Log(), wf_repo, env_repo)


# ── set use case ─────────────────────────────────────────────────────
def test_set_binds_one_section_and_persists():
    env_repo, wf_repo = MemoryEnvironmentRepo(), MemoryWorkflowEnvironmentRepo()
    dev = _env(env_repo, "Log Check", "DEV"); qas = _env(env_repo, "Log Check", "QAS")
    r = _run(_uc(env_repo, wf_repo).execute(SetWorkflowEnvironmentAssignmentCommand(
        tenant_id="t1", workflow_id="w1", section="Log Check", environment_ids=[dev, qas], updated_by="u")))
    assert not r.error and r.section == "Log Check" and len(r.environment_ids) == 2
    got = wf_repo.get_by_workflow(tenant_id="t1", workflow_id="w1")
    assert got is not None and set(got.environment_ids) == {dev, qas}


def test_set_rejects_empty_and_env_not_in_section():
    env_repo, wf_repo = MemoryEnvironmentRepo(), MemoryWorkflowEnvironmentRepo()
    dev = _env(env_repo, "Log Check", "DEV"); uat = _env(env_repo, "Master Data", "UAT")
    uc = _uc(env_repo, wf_repo)
    r = _run(uc.execute(SetWorkflowEnvironmentAssignmentCommand(
        tenant_id="t1", workflow_id="w1", section="Log Check", environment_ids=[], updated_by="u")))
    assert r.error_code == ErrorCode.WORKFLOW_ENVIRONMENT_INVALID.code
    r = _run(uc.execute(SetWorkflowEnvironmentAssignmentCommand(
        tenant_id="t1", workflow_id="w1", section="Log Check", environment_ids=[dev, uat], updated_by="u")))
    assert r.error_code == ErrorCode.WORKFLOW_ENVIRONMENT_INVALID.code  # uat is in Master Data


def test_set_is_replace_and_change_section():
    env_repo, wf_repo = MemoryEnvironmentRepo(), MemoryWorkflowEnvironmentRepo()
    dev = _env(env_repo, "Log Check", "DEV"); qas = _env(env_repo, "Log Check", "QAS")
    uat = _env(env_repo, "Master Data", "UAT")
    uc = _uc(env_repo, wf_repo)
    _run(uc.execute(SetWorkflowEnvironmentAssignmentCommand(tenant_id="t1", workflow_id="w1", section="Log Check", environment_ids=[dev, qas], updated_by="u")))
    r = _run(uc.execute(SetWorkflowEnvironmentAssignmentCommand(tenant_id="t1", workflow_id="w1", section="Master Data", environment_ids=[uat], updated_by="u")))
    assert not r.error and r.section == "Master Data" and r.environment_ids == [uat]
    got = wf_repo.get_by_workflow(tenant_id="t1", workflow_id="w1")
    assert got.section == "Master Data" and got.environment_ids == [uat]


# ── get / reverse ────────────────────────────────────────────────────
def test_get_unassigned_is_empty_not_error():
    env_repo, wf_repo = MemoryEnvironmentRepo(), MemoryWorkflowEnvironmentRepo()
    r = _run(GetWorkflowEnvironmentAssignmentUseCase(_Log(), wf_repo, env_repo).execute(
        GetWorkflowEnvironmentAssignmentCommand(tenant_id="t1", workflow_id="nope")))
    assert not r.error and r.assigned is False and r.environment_ids == []


def test_get_derives_section_after_rename():
    # SA-2481: renaming a section in Config must be reflected on read (the section
    # is derived from the assigned envs' CURRENT section, not the stored name).
    env_repo, wf_repo = MemoryEnvironmentRepo(), MemoryWorkflowEnvironmentRepo()
    dev = _env(env_repo, "OldSec", "DEV")
    _run(_uc(env_repo, wf_repo).execute(SetWorkflowEnvironmentAssignmentCommand(
        tenant_id="t1", workflow_id="w1", section="OldSec", environment_ids=[dev], updated_by="u")))
    # rename the section on the env (what a Config rename does)
    env = env_repo.get_by_id(dev)
    env.set_section("NewSec", updated_by="u")
    env_repo.save(env, expected_version=env.version - 1)
    r = _run(GetWorkflowEnvironmentAssignmentUseCase(_Log(), wf_repo, env_repo).execute(
        GetWorkflowEnvironmentAssignmentCommand(tenant_id="t1", workflow_id="w1")))
    assert r.assigned is True and r.section == "NewSec" and r.environment_ids == [dev]


def test_reverse_lookup():
    env_repo, wf_repo = MemoryEnvironmentRepo(), MemoryWorkflowEnvironmentRepo()
    dev = _env(env_repo, "Log Check", "DEV")
    _run(_uc(env_repo, wf_repo).execute(SetWorkflowEnvironmentAssignmentCommand(tenant_id="t1", workflow_id="w1", section="Log Check", environment_ids=[dev], updated_by="u")))
    r = _run(ListWorkflowsForEnvironmentUseCase(_Log(), wf_repo).execute(
        ListWorkflowsForEnvironmentCommand(tenant_id="t1", environment_id=dev)))
    assert r.total == 1 and r.workflow_ids == ["w1"]


def test_repo_one_active_per_workflow():
    wf_repo = MemoryWorkflowEnvironmentRepo()
    a = WorkflowEnvironmentAssignment.create(tenant_id="t1", workflow_id="w1", section="S", environment_ids=["e1"], created_by="u")
    wf_repo.save(a)
    b = WorkflowEnvironmentAssignment.create(tenant_id="t1", workflow_id="w1", section="S", environment_ids=["e2"], created_by="u")
    with pytest.raises(CASMismatch):
        wf_repo.save(b)


# ── run guard ────────────────────────────────────────────────────────
def test_guard():
    wf_repo = MemoryWorkflowEnvironmentRepo()
    a = WorkflowEnvironmentAssignment.create(tenant_id="t1", workflow_id="w1", section="S", environment_ids=["dev", "qas"], created_by="u")
    wf_repo.save(a)
    g = lambda wid, env: check_environment_assignment(repo=wf_repo, tenant_id="t1", workflow_id=wid, environment_id=env)
    assert g("w1", "prod") is not None          # wrong env rejected
    assert g("w2", "dev") is not None            # unassigned rejects any env
    assert g("w1", "dev") is None                # assigned env allowed
    assert g("w1", "") is None                   # empty allowed
    assert g("w1", "__inherit__") is None        # subflow inherit allowed
    assert check_environment_assignment(repo=None, tenant_id="t1", workflow_id="w1", environment_id="x") is None  # dormant
