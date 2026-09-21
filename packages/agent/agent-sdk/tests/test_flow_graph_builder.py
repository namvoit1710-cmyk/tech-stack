import asyncio
import inspect

import pytest


class TestFlowGraphBuilderSequential:
    def test_build_sequential_flow(self):
        from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
        from agent_sdk.layer2_application.services.node_type_registry import (
            NodeTypeRegistry,
        )
        from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
            FlowGraphBuilder,
        )

        registry = NodeTypeRegistry()

        def echo_node(state: dict, step_config: StepConfig, deps: dict) -> dict:
            return {"last_step": step_config.id}

        registry.register("echo", echo_node)
        flow_config = FlowConfig(
            agent_type="test_agent",
            flow_type="main",
            steps=[
                StepConfig(id="step_a", type="echo"),
                StepConfig(id="step_b", type="echo"),
            ],
        )
        builder = FlowGraphBuilder(registry=registry, deps={})
        compiled = builder.build(flow_config)
        assert compiled is not None
        output = compiled.invoke({"message": "hello"})
        assert output["last_step"] == "step_b"


class TestFlowGraphBuilderEmptyFlow:
    def test_empty_flow_raises(self):
        from agent_sdk.layer1_domain.entities.flow_config import FlowConfig
        from agent_sdk.layer2_application.services.node_type_registry import (
            NodeTypeRegistry,
        )
        from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
            FlowGraphBuilder,
        )

        registry = NodeTypeRegistry()
        flow_config = FlowConfig(agent_type="test_agent", flow_type="main", steps=[])
        builder = FlowGraphBuilder(registry=registry, deps={})
        with pytest.raises(ValueError, match="[Ee]mpty|[Nn]o steps"):
            builder.build(flow_config)


class TestFlowGraphBuilderUnknownType:
    def test_unknown_step_type_raises(self):
        from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
        from agent_sdk.layer2_application.services.node_type_registry import (
            NodeTypeRegistry,
        )
        from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
            FlowGraphBuilder,
        )

        registry = NodeTypeRegistry()
        flow_config = FlowConfig(
            agent_type="test_agent",
            flow_type="main",
            steps=[StepConfig(id="step_a", type="nonexistent_type")],
        )
        builder = FlowGraphBuilder(registry=registry, deps={})
        with pytest.raises(ValueError, match="[Uu]nknown|[Uu]nregistered"):
            builder.build(flow_config)


class TestNodeTypeRegistry:
    def test_node_type_registry(self):
        from agent_sdk.layer1_domain.entities.flow_config import StepConfig
        from agent_sdk.layer2_application.services.node_type_registry import (
            NodeTypeRegistry,
        )

        registry = NodeTypeRegistry()
        assert registry.list_types() == []
        assert not registry.has("my_type")

        def my_fn(state: dict, step_config: StepConfig, deps: dict) -> dict:
            return {}

        registry.register("my_type", my_fn)
        assert registry.has("my_type")
        assert registry.get("my_type") is my_fn
        assert "my_type" in registry.list_types()
        assert registry.get("unknown") is None


class TestFlowGraphBuilderTypeAnnotations:
    def test_build_has_type_annotations(self):
        from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
            FlowGraphBuilder,
        )

        sig = inspect.signature(FlowGraphBuilder.build)
        params = sig.parameters
        assert (
            params["interrupt_before"].annotation != inspect.Parameter.empty
        ), "interrupt_before must have a type annotation"
        assert (
            params["interrupt_after"].annotation != inspect.Parameter.empty
        ), "interrupt_after must have a type annotation"
        assert (
            sig.return_annotation != inspect.Parameter.empty
        ), "build must have a return type annotation"


class TestFlowGraphBuilderSubgraph:
    def test_flow_graph_builder_subgraph(self):
        from langgraph.graph import END, StateGraph

        from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
        from agent_sdk.layer2_application.services.node_type_registry import (
            NodeTypeRegistry,
        )
        from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
            FlowGraphBuilder,
        )

        sub_builder = StateGraph(dict)
        sub_builder.add_node("sub_node", lambda state: {"sub": True})
        sub_builder.add_edge("sub_node", END)
        sub_builder.set_entry_point("sub_node")
        subgraph = sub_builder.compile()

        config = FlowConfig(
            agent_type="test",
            flow_type="test",
            steps=[StepConfig(id="step1", type="subgraph", subflow_ref="my_sub")],
        )

        builder = FlowGraphBuilder(registry=NodeTypeRegistry())
        graph = builder.build(config, subgraphs={"my_sub": subgraph})

        result = graph.invoke({})
        assert result.get("sub") is True

    def test_flow_graph_builder_rejects_uncompiled_subgraph_value(self):
        from langgraph.graph import END, StateGraph

        from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
        from agent_sdk.layer2_application.services.node_type_registry import (
            NodeTypeRegistry,
        )
        from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
            FlowGraphBuilder,
        )

        raw = StateGraph(dict)
        raw.add_node("step", lambda state: {"ok": True})
        raw.set_entry_point("step")
        raw.add_edge("step", END)
        config = FlowConfig(
            agent_type="test",
            flow_type="test",
            steps=[StepConfig(id="child", type="subgraph", subflow_ref="x")],
        )
        builder = FlowGraphBuilder(registry=NodeTypeRegistry())
        with pytest.raises(TypeError, match="compiled subgraph"):
            builder.build(config, subgraphs={"x": raw})


# ─────────────────────────────────────────────────────────────────────────────
# FlowGraphBuilder must not auto-emit node lifecycle events
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flow_graph_builder_emits_node_started_and_completed():
    """FlowGraphBuilder must not auto-emit NODE_STARTED/NODE_COMPLETED for sync steps."""
    from unittest.mock import AsyncMock

    from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
    from agent_sdk.layer2_application.services.node_type_registry import (
        NodeTypeRegistry,
    )
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )
    from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
        FlowGraphBuilder,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    registry = NodeTypeRegistry()

    def echo_step(state: dict, step_config: StepConfig, deps: dict) -> dict:
        return {"stepped": step_config.id}

    registry.register("echo", echo_step)
    flow_config = FlowConfig(
        agent_type="test",
        flow_type="main",
        steps=[StepConfig(id="step_a", type="echo")],
    )
    builder = FlowGraphBuilder(registry=registry, deps={})
    compiled = builder.build(flow_config)

    with workflow_event_scope(emitter):
        compiled.invoke({"message": "hi"})
    await asyncio.sleep(0)

    published_types = {
        call[0][1].get("event_type") for call in mock_publisher.publish.call_args_list
    }
    assert "NODE_STARTED" not in published_types
    assert "NODE_COMPLETED" not in published_types


@pytest.mark.asyncio
async def test_flow_graph_builder_async_step_emits_node_events():
    """FlowGraphBuilder must not auto-emit NODE_STARTED/NODE_COMPLETED for async steps."""
    from unittest.mock import AsyncMock

    from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
    from agent_sdk.layer2_application.services.node_type_registry import (
        NodeTypeRegistry,
    )
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )
    from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
        FlowGraphBuilder,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    registry = NodeTypeRegistry()

    async def async_step(state: dict, step_config: StepConfig, deps: dict) -> dict:
        return {"async_stepped": step_config.id}

    registry.register("async_echo", async_step)
    flow_config = FlowConfig(
        agent_type="test",
        flow_type="main",
        steps=[StepConfig(id="async_a", type="async_echo")],
    )
    builder = FlowGraphBuilder(registry=registry, deps={})
    compiled = builder.build(flow_config)

    with workflow_event_scope(emitter):
        await compiled.ainvoke({"message": "hi"})

    published_types = {
        call[0][1].get("event_type") for call in mock_publisher.publish.call_args_list
    }
    assert "NODE_STARTED" not in published_types
    assert "NODE_COMPLETED" not in published_types


@pytest.mark.asyncio
async def test_flow_graph_builder_step_events_carry_step_id():
    """FlowGraphBuilder must not auto-emit step lifecycle payloads."""
    from unittest.mock import AsyncMock

    from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
    from agent_sdk.layer2_application.services.node_type_registry import (
        NodeTypeRegistry,
    )
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )
    from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
        FlowGraphBuilder,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    registry = NodeTypeRegistry()

    def my_step(state: dict, step_config: StepConfig, deps: dict) -> dict:
        return {}

    registry.register("my_type", my_step)
    flow_config = FlowConfig(
        agent_type="test",
        flow_type="main",
        steps=[StepConfig(id="my_step_id", type="my_type")],
    )
    builder = FlowGraphBuilder(registry=registry, deps={})
    compiled = builder.build(flow_config)

    with workflow_event_scope(emitter):
        compiled.invoke({})
    await asyncio.sleep(0)

    node_events = [
        call[0][1]
        for call in mock_publisher.publish.call_args_list
        if call[0][1].get("event_type") in ("NODE_STARTED", "NODE_COMPLETED")
    ]
    assert node_events == []


def test_flow_graph_builder_no_events_without_scope():
    """Without active workflow_event_scope, _wrap_step must not raise."""
    from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
    from agent_sdk.layer2_application.services.node_type_registry import (
        NodeTypeRegistry,
    )
    from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
        FlowGraphBuilder,
    )

    registry = NodeTypeRegistry()

    def quiet_step(state: dict, step_config: StepConfig, deps: dict) -> dict:
        return {"quiet": True}

    registry.register("quiet", quiet_step)
    flow_config = FlowConfig(
        agent_type="test",
        flow_type="main",
        steps=[StepConfig(id="q1", type="quiet")],
    )
    builder = FlowGraphBuilder(registry=registry, deps={})
    compiled = builder.build(flow_config)
    output = compiled.invoke({})
    assert output["quiet"] is True


def test_fire_and_forget_does_not_raise_when_no_running_loop():
    """_fire_and_forget must silently skip event emission when no running event loop exists."""
    from unittest.mock import MagicMock

    from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
    from agent_sdk.layer2_application.services.node_type_registry import (
        NodeTypeRegistry,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )
    from agent_sdk.layer4_frameworks.graph.flow_graph_builder import FlowGraphBuilder

    registry = NodeTypeRegistry()

    def quiet_step(state: dict, step_config: StepConfig, deps: dict) -> dict:
        return {"ok": True}

    registry.register("quiet2", quiet_step)
    flow_config = FlowConfig(
        agent_type="test",
        flow_type="main",
        steps=[StepConfig(id="q2", type="quiet2")],
    )
    builder = FlowGraphBuilder(registry=registry, deps={})
    compiled = builder.build(flow_config)

    mock_emitter = MagicMock()
    with workflow_event_scope(mock_emitter):
        result = compiled.invoke({})
    assert result["ok"] is True


def test_router_returns_end_on_no_branch_match():
    """Router must return END (not '') when branch has no match and no default."""

    from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
    from agent_sdk.layer2_application.services.node_type_registry import (
        NodeTypeRegistry,
    )
    from agent_sdk.layer4_frameworks.graph.flow_graph_builder import FlowGraphBuilder

    registry = NodeTypeRegistry()

    def router_step(state: dict, step_config: StepConfig, deps: dict) -> dict:
        return {step_config.id: {"branch": "nonexistent_branch"}}

    def target_step(state: dict, step_config: StepConfig, deps: dict) -> dict:
        return {"reached": True}

    registry.register("router", router_step)
    registry.register("target", target_step)

    flow_config = FlowConfig(
        agent_type="test",
        flow_type="main",
        steps=[
            StepConfig(
                id="my_router",
                type="router",
                routes={"known_branch": "my_target"},
            ),
            StepConfig(id="my_target", type="target"),
        ],
    )
    builder = FlowGraphBuilder(registry=registry, deps={})
    compiled = builder.build(flow_config)
    result = compiled.invoke({})
    assert result.get("reached") is not True


def test_router_only_captures_valid_routes():
    """_route closure must capture valid_routes (filtered), not original routes dict."""
    from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
    from agent_sdk.layer2_application.services.node_type_registry import (
        NodeTypeRegistry,
    )
    from agent_sdk.layer4_frameworks.graph.flow_graph_builder import FlowGraphBuilder

    registry = NodeTypeRegistry()

    def router_step(state: dict, step_config: StepConfig, deps: dict) -> dict:
        return {step_config.id: {"branch": "ghost_branch"}}

    def real_step(state: dict, step_config: StepConfig, deps: dict) -> dict:
        return {"real": True}

    registry.register("router", router_step)
    registry.register("real", real_step)

    flow_config = FlowConfig(
        agent_type="test",
        flow_type="main",
        steps=[
            StepConfig(
                id="my_router",
                type="router",
                routes={
                    "real_branch": "real_step",
                    "ghost_branch": "nonexistent_step_id",
                },
            ),
            StepConfig(id="real_step", type="real"),
        ],
    )
    builder = FlowGraphBuilder(registry=registry, deps={})
    compiled = builder.build(flow_config)
    result = compiled.invoke({})
    assert "real" not in result
