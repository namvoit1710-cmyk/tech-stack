"""SA-2371 — {{$env.*}} resolution at StartRun.

The selected environment's non-secret variables are injected as the ``$env``
source into the run context; node fields then resolve ``{{$env.KEY}}`` through
the existing VariableResolver. Secret (credential-type) variables are excluded
from the persisted context.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.layer1_domain.entities.environment import Environment
from app.layer1_domain.value_objects.env_variable import EnvVariable
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
    StartRunUseCase,
)
from app.layer2_application.resolve.variable_resolver import (
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer4_frameworks.persistence.memory.memory_environment_repo import (
    MemoryEnvironmentRepo,
)


class _Log:
    def info(self, *a, **k): ...
    def warning(self, *a, **k): ...
    def error(self, *a, **k): ...
    def debug(self, *a, **k): ...


def _env(tenant="t1"):
    return Environment.create(
        tenant_id=tenant, section="SimpleMDG", name="qas", created_by="rowan",
        variables=[
            EnvVariable.make(key="base_url", type="text", value="https://srv-qas"),
            EnvVariable.make(key="LOG_ACCOUNT", type="text", value="svc-qas"),
            EnvVariable.make(key="CLAUDE_TOKENS", type="list", values=["a", "b"]),
            EnvVariable.make(key="auth", type="credential", value="cred_qas"),
        ],
    )


def test_non_secret_env_map_includes_credential_ref_not_secret():
    m = _env().non_secret_env_map()
    assert m == {
        "base_url": "https://srv-qas",
        "LOG_ACCOUNT": "svc-qas",
        "CLAUDE_TOKENS": ["a", "b"],
        # credential var maps to its REFERENCE id (non-secret), so a node can do
        # credential_ref: {{$env.auth}}. The secret itself is never in the map.
        "auth": "cred_qas",
    }
    assert m["auth"] == "cred_qas"  # the ref id, NOT a secret value


def test_suggestion_env_map_masks_credential_id():
    # The design-time panel offers the VARIABLE key to drag ({{$env.auth}}); the
    # credential it links to is not shown, so its ref id is masked to None.
    m = _env().suggestion_env_map()
    assert m["base_url"] == "https://srv-qas"     # text keeps its value hint
    assert m["CLAUDE_TOKENS"] == ["a", "b"]        # list keeps its pool
    assert "auth" in m and m["auth"] is None        # credential key present, id hidden


@pytest.mark.asyncio
async def test_resolver_resolves_env_source():
    resolver = VariableResolver([TraversalResolverStrategy()])
    context = {"env": _env().non_secret_env_map()}
    assert await resolver.resolve("{{$env.base_url}}", context) == "https://srv-qas"
    # embedded in a larger string
    url = await resolver.resolve("{{$env.base_url}}/SystemProcessService", context)
    assert url == "https://srv-qas/SystemProcessService"
    # list element
    assert await resolver.resolve("{{$env.CLAUDE_TOKENS[1]}}", context) == "b"


def _use_case(repo):
    # Only _bind_environment is exercised; the orchestration collaborators are
    # unused for it, so pass Nones for the required positional deps.
    return StartRunUseCase(
        logger=_Log(),
        workflow_repo=None,
        workflow_snapshot_repo=None,
        run_repo=None,
        task_repo=None,
        orchestration_engine=None,
        stage_recorder=None,
        environment_repo=repo,
    )


def _fake_run():
    return SimpleNamespace(id="run-1", context=SimpleNamespace(variables={}))


def test_bind_environment_injects_env_source():
    repo = MemoryEnvironmentRepo()
    env = _env()
    repo.save(env)
    uc = _use_case(repo)
    run = _fake_run()
    uc._bind_environment(run, StartRunCommand(
        workflow_id="wf", tenant_id="t1", environment_id=env.id))
    assert run.context.variables["env"]["base_url"] == "https://srv-qas"
    # credential var is present as its ref id (non-secret), usable as credential_ref
    assert run.context.variables["env"]["auth"] == "cred_qas"


def test_bind_environment_noop_without_id():
    uc = _use_case(MemoryEnvironmentRepo())
    run = _fake_run()
    uc._bind_environment(run, StartRunCommand(workflow_id="wf", tenant_id="t1"))
    assert "env" not in run.context.variables


def test_bind_environment_tenant_isolation():
    repo = MemoryEnvironmentRepo()
    env = _env(tenant="t1")
    repo.save(env)
    uc = _use_case(repo)
    run = _fake_run()
    # different tenant requesting the same env id -> not bound
    uc._bind_environment(run, StartRunCommand(
        workflow_id="wf", tenant_id="other", environment_id=env.id))
    assert "env" not in run.context.variables
