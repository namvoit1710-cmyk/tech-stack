"""Unit spec — RegistryEndpointResolver gap-fill (registry client).

Gap-fill sweep 2026-07-02 (unit-smith). registry_endpoint_resolver.py sat at
87%. The existing test_registry_endpoint_resolver.py covers invoke_endpoint,
configuration.endpoint_url, health_endpoint fallback, healthcheck_endpoint
fallback, and the "no active agent" raise. Uncovered branches filled here
(grounded in source
``agent_sdk/layer4_frameworks/registry/registry_endpoint_resolver.py``):
  - metadata.endpoint_url branch (lines 22-26)
  - top-level endpoint_url branch (lines 32-34)
  - skip non-matching agent id then match a later one (line 16 continue)
  - matched agent but NO endpoint anywhere -> ValueError (line 42)

Five case types: happy · edge · invalid input · boundary · failure path.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer4_frameworks.registry.registry_endpoint_resolver import (
    RegistryEndpointResolver,
)


def _resolver(agents):
    registry = MagicMock()
    registry.list_active_agents = AsyncMock(return_value=agents)
    return RegistryEndpointResolver(registry)


# ── happy path: metadata.endpoint_url ──────────────────────────────────────
@pytest.mark.asyncio
async def test_resolves_via_metadata_endpoint_url():
    # Source: metadata dict -> metadata.get("endpoint_url").
    resolver = _resolver(
        [{"id": "a1", "metadata": {"endpoint_url": "http://a1:9000"}}]
    )
    assert await resolver.resolve_endpoint("a1") == "http://a1:9000"


# ── happy path: top-level endpoint_url ─────────────────────────────────────
@pytest.mark.asyncio
async def test_resolves_via_top_level_endpoint_url():
    # Source: agent.get("endpoint_url") after config/metadata miss.
    resolver = _resolver([{"agent_id": "a2", "endpoint_url": "http://a2:9001"}])
    assert await resolver.resolve_endpoint("a2") == "http://a2:9001"


# ── edge: skip non-matching agent, then match a later entry ────────────────
@pytest.mark.asyncio
async def test_skips_non_matching_agents_then_matches():
    # Source: `if current_id != agent_id: continue` (line 16).
    resolver = _resolver(
        [
            {"id": "other", "invoke_endpoint": "http://other:1"},
            {"id": "wanted", "invoke_endpoint": "http://wanted:2"},
        ]
    )
    assert await resolver.resolve_endpoint("wanted") == "http://wanted:2"


# ── boundary: metadata that is not a dict is ignored, next branch tried ────
@pytest.mark.asyncio
async def test_non_dict_metadata_ignored_and_falls_through_to_endpoint_url():
    # Source: `if isinstance(metadata, dict)` guard; metadata="" is falsy-or {}.
    resolver = _resolver(
        [{"id": "a3", "metadata": None, "endpoint_url": "http://a3:9002"}]
    )
    assert await resolver.resolve_endpoint("a3") == "http://a3:9002"


# ── failure path: matched agent but no endpoint anywhere -> ValueError ─────
@pytest.mark.asyncio
async def test_matched_agent_without_any_endpoint_raises():
    # Source line 42: raise ValueError("No endpoint URL found for active agent_id ...").
    resolver = _resolver([{"id": "a4"}])  # no endpoint fields at all
    with pytest.raises(ValueError, match="No endpoint URL found for active agent_id 'a4'"):
        await resolver.resolve_endpoint("a4")


# ── invalid input: health endpoint that does NOT end with /health raises ───
@pytest.mark.asyncio
async def test_health_endpoint_without_health_suffix_raises():
    # Source: only strips suffix when endswith("/health"); else falls to raise.
    resolver = _resolver([{"id": "a5", "health_endpoint": "http://a5:9003/status"}])
    with pytest.raises(ValueError, match="No endpoint URL found"):
        await resolver.resolve_endpoint("a5")
