from langchain_core.tools import BaseTool


def test_tool_decorator_without_hints_returns_base_tool():
    from agent_sdk.layer4_frameworks.ai.local_tool import tool

    @tool
    def my_tool(x: int) -> int:
        """Double the input."""
        return x * 2

    assert isinstance(my_tool, BaseTool)


def test_tool_decorator_with_registry_hints_preserves_langchain_behavior():
    from agent_sdk.layer4_frameworks.ai.local_tool import tool

    @tool(
        registry_name="executor_service__executor-runtime__my_tool__1.0.0",
        registry_owner={"kind": "executor_service", "name": "executor-runtime"},
        registry_version="1.0.0",
        registry_metadata={"scope": "executor_service"},
    )
    def my_tool(x: int) -> int:
        """Double the input."""
        return x * 2

    assert isinstance(my_tool, BaseTool)
    assert my_tool.metadata["agent_sdk_registry"] == {
        "registry_name": "executor_service__executor-runtime__my_tool__1.0.0",
        "registry_owner": {
            "kind": "executor_service",
            "name": "executor-runtime",
        },
        "registry_version": "1.0.0",
        "metadata": {"scope": "executor_service"},
    }
