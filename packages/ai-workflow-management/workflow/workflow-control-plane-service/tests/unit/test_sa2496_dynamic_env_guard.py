"""SA-2496 — "Dynamic environment (only id)" for the call-subflow node.

The ChildWorkflowCoordinator guards a DYNAMIC (expression-resolved) environment
id against the target sub-workflow's SA-2450 assignment:

* dynamic id NOT in the subflow's assignment (a wrong id, or a NAME/label since
  the assignment holds ids only) → node fails (ENVIRONMENT_NOT_ASSIGNED).
* dynamic id in the assignment → binds normally.
* a LITERAL id (non-dynamic) keeps its existing SA-2458 behavior (no new guard).
* inherit / empty are always allowed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.layer1_domain.exceptions import WorkflowExecutionError
from app.layer2_application.orchestration.child_workflow_coordinator import (
    ChildWorkflowCoordinator,
)


class _FakeAssignmentRepo:
    def __init__(self, environment_ids):
        self._ids = environment_ids

    def get_by_workflow(self, *, tenant_id, workflow_id):  # noqa: ARG002
        if self._ids is None:
            return None
        return SimpleNamespace(environment_ids=list(self._ids))


class _FakeEnv:
    def __init__(self, env_id, tenant_id):
        self.id = env_id
        self.tenant_id = tenant_id
        self.is_active = True
        self.section = "Root Cause"
        self.name = "QAS"

    def non_secret_env_map(self):
        return {"BASE_URL": "https://qas.example"}


class _FakeEnvRepo:
    def __init__(self, env):
        self._env = env

    def get_by_id(self, env_id):  # noqa: ARG002
        return self._env


def _coord(*, assignment_ids, env_repo=None):
    return ChildWorkflowCoordinator(
        engine=SimpleNamespace(),
        run_repo=None,
        task_repo=None,
        environment_repo=env_repo,
        workflow_environment_repo=_FakeAssignmentRepo(assignment_ids),
    )


def _runs():
    child = SimpleNamespace(
        tenant_id="t1", workflow_id="subflow-1",
        context=SimpleNamespace(variables={}),
    )
    parent = SimpleNamespace(context=SimpleNamespace(variables={}))
    return child, parent


def test_dynamic_id_not_in_assignment_raises():
    child, parent = _runs()
    coord = _coord(assignment_ids=["env-9f3a", "env-7c0e"])
    with pytest.raises(WorkflowExecutionError) as exc:
        coord._bind_child_environment(child, parent, "env-prod9", is_dynamic=True)
    assert "ENVIRONMENT_NOT_ASSIGNED" in str(exc.value)


def test_dynamic_name_is_rejected_id_only():
    # A NAME ("qas") is never an assigned id → rejected (id-only).
    child, parent = _runs()
    coord = _coord(assignment_ids=["env-9f3a", "env-7c0e"])
    with pytest.raises(WorkflowExecutionError):
        coord._bind_child_environment(child, parent, "qas", is_dynamic=True)


def test_dynamic_unassigned_subflow_raises():
    child, parent = _runs()
    coord = _coord(assignment_ids=None)  # no assignment row
    with pytest.raises(WorkflowExecutionError):
        coord._bind_child_environment(child, parent, "env-9f3a", is_dynamic=True)


def test_dynamic_id_in_assignment_binds():
    child, parent = _runs()
    coord = _coord(
        assignment_ids=["env-9f3a", "env-7c0e"],
        env_repo=_FakeEnvRepo(_FakeEnv("env-7c0e", "t1")),
    )
    coord._bind_child_environment(child, parent, "env-7c0e", is_dynamic=True)
    assert child.context.variables.get("env") == {"BASE_URL": "https://qas.example"}


def test_literal_id_not_guarded():
    # Non-dynamic literal id keeps SA-2458 behavior: no assignment guard, so a
    # not-assigned id does NOT raise (it just binds / no-ops via env repo).
    child, parent = _runs()
    coord = _coord(
        assignment_ids=["env-9f3a"],
        env_repo=_FakeEnvRepo(_FakeEnv("env-zzzz", "t1")),
    )
    coord._bind_child_environment(child, parent, "env-zzzz", is_dynamic=False)
    assert child.context.variables.get("env") == {"BASE_URL": "https://qas.example"}


def test_inherit_and_empty_allowed():
    child, parent = _runs()
    parent.context.variables["env"] = {"FROM": "parent"}
    coord = _coord(assignment_ids=["env-9f3a"])
    coord._bind_child_environment(child, parent, "__inherit__", is_dynamic=True)
    assert child.context.variables["env"] == {"FROM": "parent"}

    child2, parent2 = _runs()
    coord._bind_child_environment(child2, parent2, "", is_dynamic=True)
    assert "env" not in child2.context.variables
