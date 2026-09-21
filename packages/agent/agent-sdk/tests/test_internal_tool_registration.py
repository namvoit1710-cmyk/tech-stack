from __future__ import annotations

from typing import Any

import pytest

from agent_sdk.layer4_frameworks.ai.local_tool import tool
from agent_sdk.layer4_frameworks.registry.tool_registrar import ToolRegistrar


class _FakeToolRegistry:
    def __init__(self) -> None:
        self.registrations: list[Any] | None = None

    async def sync_tools(self, registrations: list[Any]) -> dict[str, str]:
        self.registrations = registrations
        return {
            registration.tool_name: f"tool-id-{registration.tool_name}"
            for registration in registrations
        }

    async def activate_tools(self, tool_ids: list[str]) -> None:
        return None

    async def deactivate_tools(self, tool_ids: list[str]) -> None:
        return None

    async def close(self) -> None:
        return None


@tool(internal=True)
def _internal_calculation(value: int) -> int:
    """Internal helper that should be available locally only."""
    return value + 1


@tool
def _public_echo(message: str) -> str:
    """Public helper that should be registered."""
    return message


@tool(internal=False)
def _explicit_public_echo(message: str) -> str:
    """Explicitly public helper that should still be registered."""
    return message


def test_tool_internal_true_is_stored_as_registry_hint() -> None:
    metadata = getattr(_internal_calculation, "metadata", {})

    assert metadata["agent_sdk_registry"]["internal"] is True


@pytest.mark.asyncio
async def test_tool_registrar_skips_internal_decorator_tools() -> None:
    registry = _FakeToolRegistry()
    registrar = ToolRegistrar(registry)

    result = await registrar.register_tools(
        [_internal_calculation, _public_echo, _explicit_public_echo],
        owner_kind="agent",
        owner_name="demo-agent",
        owner_version="1.0.0",
    )

    assert registry.registrations is not None
    registered_names = [
        registration.tool_name for registration in registry.registrations
    ]
    assert registered_names == ["_public_echo", "_explicit_public_echo"]
    assert result == {
        "_public_echo": "tool-id-_public_echo",
        "_explicit_public_echo": "tool-id-_explicit_public_echo",
    }


@pytest.mark.asyncio
async def test_tool_registrar_accepts_string_internal_true_hint() -> None:
    @tool(internal="true")
    def string_internal_tool() -> str:
        """String truthy internal hint should be skipped."""
        return "hidden"

    @tool(internal="false")
    def string_public_tool() -> str:
        """String false internal hint should not be skipped."""
        return "visible"

    registry = _FakeToolRegistry()
    registrar = ToolRegistrar(registry)

    await registrar.register_tools(
        [string_internal_tool, string_public_tool],
        owner_kind="agent",
        owner_name="demo-agent",
        owner_version="1.0.0",
    )

    assert registry.registrations is not None
    assert [registration.tool_name for registration in registry.registrations] == [
        "string_public_tool"
    ]
