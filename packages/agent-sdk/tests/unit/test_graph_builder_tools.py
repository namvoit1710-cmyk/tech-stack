from unittest.mock import patch

from langchain_core.tools import tool as langchain_tool
from langgraph.prebuilt import ToolNode


@langchain_tool
def dummy_tool(x: int) -> str:
    """Return x as a string."""
    return str(x)


class TestAgentGraphBuilderToolSupport:
    def test_add_tools_method_exists(self):
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        builder = AgentGraphBuilder()
        assert hasattr(builder, "add_tools")

    def test_add_tool_node_method_exists(self):
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        builder = AgentGraphBuilder()
        assert hasattr(builder, "add_tool_node")

    def test_add_tools_condition_method_exists(self):
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        builder = AgentGraphBuilder()
        assert hasattr(builder, "add_tools_condition")

    def test_add_tools_returns_builder(self):
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        builder = AgentGraphBuilder()
        result = builder.add_tools([dummy_tool])
        assert result is builder

    def test_add_tool_node_returns_builder(self):
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        builder = AgentGraphBuilder()
        builder.add_tools([dummy_tool])
        result = builder.add_tool_node()
        assert result is builder

    def test_add_tools_condition_returns_builder(self):
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        builder = AgentGraphBuilder()
        builder.add_tools([dummy_tool])
        result = builder.add_tools_condition("agent")
        assert result is builder

    def test_tool_node_is_not_wrapped(self):
        """ToolNode (1-arg callable) must be passed to graph.add_node without wrapping."""
        import operator
        from typing import Annotated, TypedDict

        from langchain_core.messages import BaseMessage
        from langgraph.graph import StateGraph

        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        class State(TypedDict):
            messages: Annotated[list[BaseMessage], operator.add]

        tool_node = ToolNode([dummy_tool])

        captured_nodes = []

        original_add_node = StateGraph.add_node

        def capturing_add_node(self_inner, name, fn=None, **kwargs):
            if fn is not None:
                captured_nodes.append((name, fn))
            return original_add_node(self_inner, name, fn, **kwargs)

        with patch.object(StateGraph, "add_node", capturing_add_node):
            builder = AgentGraphBuilder(state_schema=State)
            builder.add_tools([dummy_tool])
            builder.add_tool_node("tools")

            def agent_node(state, deps):
                return {}

            builder.add_node("agent", agent_node)
            builder.set_entry_point("agent")
            builder.add_tools_condition("agent")
            builder.compile()

        tools_fn = next((fn for name, fn in captured_nodes if name == "tools"), None)
        assert tools_fn is not None, "tools node must be added"
        assert (
            tools_fn is tool_node or isinstance(tools_fn, ToolNode)
        ), "tools node must not be wrapped by _node(); it should be the ToolNode directly"

    def test_single_param_node_not_wrapped(self):
        """A node function with only (state) param should be passed directly without wrapping."""

        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        called_with = []

        def single_param_node(state):
            called_with.append(state)
            return {}

        builder = AgentGraphBuilder()
        builder.add_node("sp_node", single_param_node)
        builder.set_entry_point("sp_node")
        compiled = builder.compile()
        compiled.invoke({})
        assert len(called_with) == 1
        assert isinstance(called_with[0], dict)

    def test_two_param_node_still_wrapped(self):
        """Existing (state, deps) nodes must still be wrapped as before."""
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        called_with = []

        def two_param_node(state, deps):
            called_with.append((state, deps))
            return {}

        deps = {"key": "value"}
        builder = AgentGraphBuilder(deps=deps)
        builder.add_node("tp_node", two_param_node)
        builder.set_entry_point("tp_node")
        compiled = builder.compile()
        compiled.invoke({})
        assert len(called_with) == 1
        state_arg, deps_arg = called_with[0]
        assert isinstance(state_arg, dict)
        assert deps_arg == deps
