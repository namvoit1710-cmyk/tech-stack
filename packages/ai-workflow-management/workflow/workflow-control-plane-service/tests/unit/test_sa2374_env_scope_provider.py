"""SA-2374 — EnvScopeProvider surfaces {{$env.*}} suggestions for the node-field picker."""
from __future__ import annotations

import pytest

from app.layer2_application.features.get_variable_suggestions.use_cases.scope_providers import (
    EnvScopeProvider,
)


async def _build(variables):
    provider = EnvScopeProvider()
    scopes = await provider.build(
        run=None, target_node_id="", target_workflow=None,
        variables=variables, upstream_node_ids=[],
    )
    return scopes[0]


@pytest.mark.asyncio
async def test_scalar_and_base_url_paths():
    scope = await _build({"env": {"base_url": "https://srv", "LOG_ACCOUNT": "svc"}})
    assert scope.scope_type == "env"
    assert scope.expression_prefix == "{{$env"
    display = {p.display_path for p in scope.paths}
    assert "{{$env.base_url}}" in display
    assert "{{$env.LOG_ACCOUNT}}" in display


@pytest.mark.asyncio
async def test_pool_variable_expands_subpaths():
    scope = await _build({
        "env": {"CLAUDE_TOKENS": {"all": ["a", "b"], "current": "a", "index": 0, "count": 2}}
    })
    display = {p.display_path for p in scope.paths}
    assert "{{$env.CLAUDE_TOKENS.current}}" in display
    assert "{{$env.CLAUDE_TOKENS.all}}" in display
    assert "{{$env.CLAUDE_TOKENS.index}}" in display
    assert "{{$env.CLAUDE_TOKENS.count}}" in display


@pytest.mark.asyncio
async def test_no_env_source_yields_empty_scope():
    # No environment selected → no `env` source → nothing to drag (the panel's
    # Environment section stays empty until a designer picks an environment).
    scope = await _build({})
    assert scope.scope_type == "env"
    assert scope.paths == []
