"""SA-2375 — run/trigger environment binding (shared load_and_bind helper).

Both StartRun and TestFullWorkflow bind the selected environment through
``load_and_bind``; this locks its contract (inject $env, pool structuring,
tenant isolation, graceful no-op) independent of either use case.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.layer1_domain.entities.environment import Environment
from app.layer1_domain.value_objects.env_variable import EnvVariable
from app.layer2_application.features.start_run.use_cases.env_pool import (
    PoolCursorRegistry,
    load_and_bind,
)
from app.layer2_application.features.test_full_workflow.use_cases.test_full_workflow_use_case import (
    TestFullWorkflowCommand,
)
from app.layer4_frameworks.persistence.memory.memory_environment_repo import (
    MemoryEnvironmentRepo,
)


class _Log:
    def info(self, *a, **k): ...
    def warning(self, *a, **k): ...


def _run():
    return SimpleNamespace(id="r", context=SimpleNamespace(variables={}))


def _repo_with_env(tenant="t1"):
    repo = MemoryEnvironmentRepo()
    repo.save(Environment.create(
        tenant_id=tenant, section="S", name="qas", created_by="r",
        variables=[
            EnvVariable.make(key="base_url", type="text", value="https://srv"),
            EnvVariable.make(key="POOL", type="list", values=["x", "y"]),
            EnvVariable.make(key="auth", type="credential", value="cred"),
        ],
    ))
    return repo


def test_command_accepts_environment_id():
    cmd = TestFullWorkflowCommand(workflow_id="wf", environment_id="env1")
    assert cmd.environment_id == "env1"


def test_load_and_bind_injects_env_with_pool_and_credential_ref():
    repo = _repo_with_env()
    env_id = next(iter(repo._store))
    run = _run()
    load_and_bind(run, tenant_id="t1", environment_id=env_id,
                  repo=repo, cursors=PoolCursorRegistry(), logger=_Log())
    src = run.context.variables["env"]
    assert src["base_url"] == "https://srv"
    assert src["POOL"]["current"] == "x" and src["POOL"]["count"] == 2
    # credential var present as its ref id (non-secret); the secret never enters
    assert src["auth"] == "cred"


def test_load_and_bind_tenant_isolation_and_noop():
    repo = _repo_with_env(tenant="t1")
    env_id = next(iter(repo._store))
    run = _run()
    load_and_bind(run, tenant_id="other", environment_id=env_id,
                  repo=repo, cursors=PoolCursorRegistry(), logger=_Log())
    assert "env" not in run.context.variables
    # no id -> no-op
    load_and_bind(run, tenant_id="t1", environment_id="",
                  repo=repo, cursors=PoolCursorRegistry(), logger=_Log())
    assert "env" not in run.context.variables
