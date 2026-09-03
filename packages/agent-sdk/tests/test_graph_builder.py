import asyncio
import inspect

import pytest
from langgraph.store.memory import InMemoryStore


def make_node(return_value: dict):
    def node(state: dict, deps: dict) -> dict:
        return return_value

    return node


def make_async_node(return_value: dict):
    async def node(state: dict, deps: dict) -> dict:
        return return_value

    return node


def always_route(destination: str):
    def router(state: dict) -> str:
        return destination

    return router


class TestAgentGraphBuilder:
    def test_build_simple_graph(self):
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        builder = AgentGraphBuilder()
        builder.add_node("node_a", make_node({"x": 1}))
        builder.set_entry_point("node_a")
        compiled = builder.compile()
        assert compiled is not None

    def test_graph_invoke(self):
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        node_a = make_node({"result": "hello"})
        builder = AgentGraphBuilder()
        builder.add_node("node_a", node_a)
        builder.set_entry_point("node_a")
        compiled = builder.compile()
        output = compiled.invoke({"message": "hi"})
        assert output["result"] == "hello"

    def test_conditional_edges(self):
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        node_a = make_node({"step": "a_done"})
        node_b = make_node({"step": "b_done"})

        def router(state: dict) -> str:
            return "go_b"

        builder = AgentGraphBuilder()
        builder.add_node("node_a", node_a)
        builder.add_node("node_b", node_b)
        builder.set_entry_point("node_a")
        builder.add_conditional_edges("node_a", router, {"go_b": "node_b"})
        compiled = builder.compile()
        output = compiled.invoke({})
        assert output["step"] == "b_done"

    def test_no_entry_point_raises(self):
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        builder = AgentGraphBuilder()
        builder.add_node("node_a", make_node({}))
        with pytest.raises((ValueError, Exception)):
            builder.compile()

    def test_no_nodes_raises(self):
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        builder = AgentGraphBuilder()
        with pytest.raises((ValueError, Exception)):
            builder.compile()

    def test_add_node_if_true(self):
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        node_a = make_node({"added": True})
        node_b = make_node({"added": True, "b_ran": True})
        builder = AgentGraphBuilder()
        builder.add_node("node_a", node_a)
        builder.add_node_if(True, "node_b", node_b)
        builder.set_entry_point("node_a")
        builder.add_edge("node_a", "node_b")
        compiled = builder.compile()
        output = compiled.invoke({})
        assert output.get("b_ran") is True

    def test_add_node_if_false(self):
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        node_a = make_node({"added": False})
        builder = AgentGraphBuilder()
        builder.add_node("node_a", node_a)
        builder.add_node_if(False, "node_b", make_node({"b_ran": True}))
        builder.set_entry_point("node_a")
        compiled = builder.compile()
        output = compiled.invoke({})
        assert "b_ran" not in output

    def test_compile_has_type_annotations(self):
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        sig = inspect.signature(AgentGraphBuilder.compile)
        params = sig.parameters
        assert (
            params["checkpointer"].annotation != inspect.Parameter.empty
        ), "checkpointer must have a type annotation"
        assert (
            params["interrupt_before"].annotation != inspect.Parameter.empty
        ), "interrupt_before must have a type annotation"
        assert (
            params["interrupt_after"].annotation != inspect.Parameter.empty
        ), "interrupt_after must have a type annotation"
        assert (
            sig.return_annotation != inspect.Parameter.empty
        ), "compile must have a return type annotation"

    def test_compile_accepts_additional_langgraph_kwargs(self):
        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        builder = AgentGraphBuilder()
        builder.add_node("node_a", make_node({"x": 1}))
        builder.set_entry_point("node_a")
        compiled = builder.compile(store=InMemoryStore())
        output = compiled.invoke({})
        assert output["x"] == 1


class TestAddSubgraph:
    def test_add_subgraph(self):
        from langgraph.graph import END, StateGraph

        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        sub_builder = StateGraph(dict)
        sub_builder.add_node("sub_node", lambda state: {"sub": True})
        sub_builder.add_edge("sub_node", END)
        sub_builder.set_entry_point("sub_node")
        subgraph = sub_builder.compile()

        builder = AgentGraphBuilder()
        builder.add_subgraph("my_subgraph", subgraph)
        builder.set_entry_point("my_subgraph")
        graph = builder.compile()

        result = graph.invoke({})
        assert result.get("sub") is True

    def test_add_subgraph_with_tools(self):
        from langchain_core.tools import tool
        from langgraph.graph import END, StateGraph

        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        @tool
        def dummy_tool() -> str:
            """A dummy tool."""
            return "dummy"

        sub_builder = StateGraph(dict)
        sub_builder.add_node("sub_node", lambda state: {"sub": True})
        sub_builder.add_edge("sub_node", END)
        sub_builder.set_entry_point("sub_node")
        subgraph = sub_builder.compile()

        builder = AgentGraphBuilder()
        builder.add_tools([dummy_tool])
        builder.add_tool_node("tools")
        builder.add_subgraph("my_subgraph", subgraph)
        builder.add_edge("tools", "my_subgraph")
        builder.set_entry_point("tools")
        graph = builder.compile()
        assert graph is not None

    def test_add_subgraph_rejects_uncompiled_graph(self):
        from langgraph.graph import END, StateGraph

        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        raw = StateGraph(dict)
        raw.add_node("step", lambda state: {"ok": True})
        raw.set_entry_point("step")
        raw.add_edge("step", END)
        builder = AgentGraphBuilder()
        with pytest.raises(TypeError, match="compiled subgraph"):
            builder.add_subgraph("bad", raw)


class TestAddMappedSubgraph:
    def test_add_mapped_subgraph_rejects_uncompiled_graph(self):
        from langgraph.graph import END, StateGraph

        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        raw = StateGraph(dict)
        raw.add_node("step", lambda state: {"ok": True})
        raw.set_entry_point("step")
        raw.add_edge("step", END)
        builder = AgentGraphBuilder()
        with pytest.raises(TypeError, match="compiled subgraph"):
            builder.add_mapped_subgraph(
                "bad",
                raw,
                map_input=lambda state: state,
                map_output=lambda out, state: out,
            )

    async def test_add_mapped_subgraph_invokes_with_mapped_io(self):
        from langgraph.graph import END, StateGraph

        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        sub_builder = StateGraph(dict)
        sub_builder.add_node(
            "sub_node", lambda state: {"bar": state["foo"] + "_processed"}
        )
        sub_builder.add_edge("sub_node", END)
        sub_builder.set_entry_point("sub_node")
        subgraph = sub_builder.compile()

        builder = AgentGraphBuilder()
        builder.add_mapped_subgraph(
            "my_sub",
            subgraph,
            map_input=lambda state: {"foo": state["input"]},
            map_output=lambda out, state: {"result": out["bar"]},
        )
        builder.set_entry_point("my_sub")
        graph = builder.compile()

        result = await graph.ainvoke({"input": "hello"})
        assert result["result"] == "hello_processed"

    def test_add_mapped_subgraph_returns_builder(self):
        from langgraph.graph import END, StateGraph

        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        sub_builder = StateGraph(dict)
        sub_builder.add_node("sub_node", lambda state: {})
        sub_builder.add_edge("sub_node", END)
        sub_builder.set_entry_point("sub_node")
        subgraph = sub_builder.compile()

        builder = AgentGraphBuilder()
        result = builder.add_mapped_subgraph(
            "my_sub",
            subgraph,
            map_input=lambda state: state,
            map_output=lambda out, state: out,
        )
        assert result is builder


# ─────────────────────────────────────────────────────────────────────────────
# AgentGraphBuilder must not auto-emit node lifecycle events
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_graph_node_emits_node_started_and_completed():
    """AgentGraphBuilder must not auto-emit NODE_STARTED/NODE_COMPLETED for async nodes."""
    from unittest.mock import AsyncMock

    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )
    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    node_ran = []

    async def async_node(state, deps):
        node_ran.append(True)
        return {"result": "async"}

    builder = AgentGraphBuilder()
    builder.add_node("node_a", async_node)
    builder.set_entry_point("node_a")
    graph = builder.compile()

    with workflow_event_scope(emitter):
        await graph.ainvoke({"message": "hi"})

    assert node_ran
    published_types = {
        call[0][1].get("event_type") for call in mock_publisher.publish.call_args_list
    }
    assert "NODE_STARTED" not in published_types
    assert "NODE_COMPLETED" not in published_types


@pytest.mark.asyncio
async def test_agent_graph_sync_node_emits_node_started_and_completed():
    """AgentGraphBuilder must not auto-emit NODE_STARTED/NODE_COMPLETED for sync nodes."""
    from unittest.mock import AsyncMock

    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )
    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    def sync_node(state, deps):
        return {"result": "sync"}

    builder = AgentGraphBuilder()
    builder.add_node("node_s", sync_node)
    builder.set_entry_point("node_s")
    graph = builder.compile()

    with workflow_event_scope(emitter):
        graph.invoke({"message": "hi"})
    await asyncio.sleep(0)

    published_types = {
        call[0][1].get("event_type") for call in mock_publisher.publish.call_args_list
    }
    assert "NODE_STARTED" not in published_types
    assert "NODE_COMPLETED" not in published_types


@pytest.mark.asyncio
async def test_agent_graph_node_events_carry_node_id():
    """AgentGraphBuilder must not auto-emit node lifecycle payloads."""
    from unittest.mock import AsyncMock

    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )
    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    async def my_node(state, deps):
        return {"x": 1}

    builder = AgentGraphBuilder()
    builder.add_node("my_node", my_node)
    builder.set_entry_point("my_node")
    graph = builder.compile()

    with workflow_event_scope(emitter):
        await graph.ainvoke({})

    node_event_payloads = [
        call[0][1]
        for call in mock_publisher.publish.call_args_list
        if call[0][1].get("event_type") in ("NODE_STARTED", "NODE_COMPLETED")
    ]
    assert node_event_payloads == []


def test_agent_graph_node_no_events_without_scope():
    """Without active workflow_event_scope, _node wrapper must not raise (events silently dropped)."""
    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    def quiet_node(state, deps):
        return {"done": True}

    builder = AgentGraphBuilder()
    builder.add_node("quiet", quiet_node)
    builder.set_entry_point("quiet")
    graph = builder.compile()
    output = graph.invoke({})
    assert output["done"] is True
