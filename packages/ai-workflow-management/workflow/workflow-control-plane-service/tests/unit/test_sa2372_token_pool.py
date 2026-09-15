"""SA-2372 — list variable + round-robin token pool selection."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.layer1_domain.entities.environment import Environment
from app.layer1_domain.value_objects.env_variable import EnvVariable
from app.layer2_application.features.start_run.use_cases.env_pool import (
    PoolCursorRegistry,
    expose_pool,
)
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


def _env():
    return Environment.create(
        tenant_id="t1", section="SimpleMDG", name="qas", created_by="rowan",
        variables=[
            EnvVariable.make(key="base_url", type="text", value="https://srv"),
            EnvVariable.make(key="CLAUDE_TOKENS", type="list", values=["a", "b", "c"]),
        ],
    )


def _use_case(repo):
    return StartRunUseCase(
        logger=_Log(), workflow_repo=None, workflow_snapshot_repo=None,
        run_repo=None, task_repo=None, orchestration_engine=None,
        stage_recorder=None, environment_repo=repo,
    )


def _fake_run():
    return SimpleNamespace(id="run-1", context=SimpleNamespace(variables={}))


def test_cursor_registry_round_robins():
    reg = PoolCursorRegistry()
    vals = ["a", "b", "c"]
    picks = [reg.select("k", vals) for _ in range(5)]
    assert [v for v, _ in picks] == ["a", "b", "c", "a", "b"]
    assert [i for _, i in picks] == [0, 1, 2, 0, 1]


def test_cursor_registry_empty_pool():
    reg = PoolCursorRegistry()
    assert reg.select("k", []) == (None, -1)


def test_expose_pool_shape():
    assert expose_pool(["a", "b"], "a", 0) == {
        "all": ["a", "b"], "current": "a", "index": 0, "count": 2,
    }


def test_bind_exposes_pool_and_rotates_across_runs():
    repo = MemoryEnvironmentRepo()
    env = _env()
    repo.save(env)
    uc = _use_case(repo)

    r1 = _fake_run()
    uc._bind_environment(r1, StartRunCommand(workflow_id="wf", tenant_id="t1", environment_id=env.id))
    pool1 = r1.context.variables["env"]["CLAUDE_TOKENS"]
    assert pool1["all"] == ["a", "b", "c"]
    assert pool1["count"] == 3
    assert pool1["current"] == "a" and pool1["index"] == 0

    r2 = _fake_run()
    uc._bind_environment(r2, StartRunCommand(workflow_id="wf", tenant_id="t1", environment_id=env.id))
    assert r2.context.variables["env"]["CLAUDE_TOKENS"]["current"] == "b"

    # non-list var stays a scalar
    assert r1.context.variables["env"]["base_url"] == "https://srv"


@pytest.mark.asyncio
async def test_resolver_reads_pool_fields():
    repo = MemoryEnvironmentRepo()
    env = _env()
    repo.save(env)
    uc = _use_case(repo)
    run = _fake_run()
    uc._bind_environment(run, StartRunCommand(workflow_id="wf", tenant_id="t1", environment_id=env.id))
    ctx = run.context.variables

    resolver = VariableResolver([TraversalResolverStrategy()])
    assert await resolver.resolve("{{$env.CLAUDE_TOKENS.current}}", ctx) == "a"
    assert await resolver.resolve("{{$env.CLAUDE_TOKENS.all[2]}}", ctx) == "c"
    # A full-match expression returns the raw value (int), not a string.
    assert await resolver.resolve("{{$env.CLAUDE_TOKENS.count}}", ctx) == 3
    # Embedded in a string, it is stringified.
    assert await resolver.resolve("pool={{$env.CLAUDE_TOKENS.count}}", ctx) == "pool=3"
