from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_inline_tool():
    from langchain_core.tools import tool as lc_tool

    @lc_tool
    def summarize(text: str) -> str:
        """Summarize the given text."""
        return text[:100]

    return summarize


@pytest.mark.asyncio
async def test_register_tools_for_executor_service():
    from agent_sdk.layer4_frameworks.registry.tool_registrar import ToolRegistrar

    tool_registry = MagicMock()
    tool_registry.sync_tools = AsyncMock(return_value={"summarize": "tool-123"})
    registrar = ToolRegistrar(tool_registry)

    result = await registrar.register_tools(
        tools=[_make_inline_tool()],
        owner_kind="executor_service",
        owner_name="executor-runtime",
        owner_version="1.0.0",
    )

    assert result == {"summarize": "tool-123"}

    registrations = tool_registry.sync_tools.await_args.args[0]
    assert len(registrations) == 1

    registration = registrations[0]
    assert registration.tool_name == "summarize"
    assert registration.registry_name == (
        "executor_service__executor-runtime__summarize__1.0.0"
    )
    assert registration.version == "1.0.0"


@pytest.mark.asyncio
async def test_register_tools_honors_registry_name_override_from_metadata():
    from agent_sdk.layer4_frameworks.registry.tool_registrar import ToolRegistrar

    tool_registry = MagicMock()
    tool_registry.sync_tools = AsyncMock(return_value={"summarize": "tool-123"})
    registrar = ToolRegistrar(tool_registry)

    tool = SimpleNamespace(
        name="summarize",
        description="Summarize the given text.",
        args_schema=None,
        metadata={
            "agent_sdk_registry": {
                "registry_name": "custom-registered-tool",
                "metadata": {"scope": "executor_service"},
            }
        },
    )

    await registrar.register_tools(
        tools=[tool],
        owner_kind="executor_service",
        owner_name="executor-runtime",
        owner_version="1.0.0",
    )

    registration = tool_registry.sync_tools.await_args.args[0][0]
    assert registration.registry_name == "custom-registered-tool"
    assert registration.metadata["scope"] == "executor_service"


@pytest.mark.asyncio
async def test_register_tools_honors_owner_and_version_hints_from_agent_sdk_tool():
    from agent_sdk.layer4_frameworks.ai.local_tool import tool
    from agent_sdk.layer4_frameworks.registry.tool_registrar import ToolRegistrar

    tool_registry = MagicMock()
    tool_registry.sync_tools = AsyncMock(return_value={"summarize": "tool-123"})
    registrar = ToolRegistrar(tool_registry)

    @tool(
        registry_owner={"kind": "executor_service", "name": "executor-runtime"},
        registry_version="2.0.0",
        registry_metadata={"scope": "executor_service"},
    )
    def summarize(text: str) -> str:
        """Summarize the given text."""
        return text[:100]

    await registrar.register_tools(
        tools=[summarize],
        owner_kind="agent",
        owner_name="planner",
        owner_version="1.0.0",
    )

    registration = tool_registry.sync_tools.await_args.args[0][0]
    assert registration.registry_name == (
        "executor_service__executor-runtime__summarize__2.0.0"
    )
    assert registration.version == "2.0.0"
    assert registration.metadata["scope"] == "executor_service"


@pytest.mark.asyncio
async def test_register_tools_propagates_registry_errors():
    from agent_sdk.layer1_domain.exceptions import RegistrationError
    from agent_sdk.layer4_frameworks.registry.tool_registrar import ToolRegistrar

    tool_registry = MagicMock()
    tool_registry.sync_tools = AsyncMock(side_effect=RegistrationError("offline"))
    registrar = ToolRegistrar(tool_registry)

    with pytest.raises(RegistrationError, match="offline"):
        await registrar.register_tools(
            tools=[_make_inline_tool()],
            owner_kind="executor_service",
            owner_name="executor-runtime",
            owner_version="1.0.0",
        )
