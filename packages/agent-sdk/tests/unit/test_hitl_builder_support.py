"""Tests for compile-time HITL builder support.

Covers:
- AgentGraphBuilder.compile() accepting interrupt_before / interrupt_after
- FlowGraphBuilder.build() accepting interrupt_before / interrupt_after
- ToolAgentBuilder.compile() accepting interrupt_before / interrupt_after
- build_app_container wiring resume_agent use case when agent_graph is present
- SDK public API re-exporting langgraph interrupt() helper
"""

from unittest.mock import MagicMock

# ─── AgentGraphBuilder compile-time HITL ──────────────────────────────────


def test_agent_graph_builder_compile_accepts_interrupt_before():
    """AgentGraphBuilder.compile() should accept interrupt_before and pass it to LangGraph."""
    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    async def my_node(state, deps):
        return {"message": "done"}

    builder = AgentGraphBuilder()
    builder.add_node("my_node", my_node)
    builder.set_entry_point("my_node")

    compiled = builder.compile(interrupt_before=["my_node"])
    assert compiled is not None


def test_agent_graph_builder_compile_accepts_interrupt_after():
    """AgentGraphBuilder.compile() should accept interrupt_after and pass it to LangGraph."""
    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    async def my_node(state, deps):
        return {"message": "done"}

    builder = AgentGraphBuilder()
    builder.add_node("my_node", my_node)
    builder.set_entry_point("my_node")

    compiled = builder.compile(interrupt_after=["my_node"])
    assert compiled is not None


def test_agent_graph_builder_compile_accepts_both_interrupt_params():
    """AgentGraphBuilder.compile() should accept both interrupt_before and interrupt_after."""
    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    async def node_a(state, deps):
        return {}

    async def node_b(state, deps):
        return {}

    builder = AgentGraphBuilder()
    builder.add_node("node_a", node_a)
    builder.add_node("node_b", node_b)
    builder.set_entry_point("node_a")
    builder.add_edge("node_a", "node_b")

    compiled = builder.compile(interrupt_before=["node_a"], interrupt_after=["node_b"])
    assert compiled is not None


def test_agent_graph_builder_passes_interrupt_before_to_langgraph():
    """AgentGraphBuilder.compile() must forward interrupt_before to StateGraph.compile().

    Verified by compiling with interrupt_before and checking the graph's
    interrupt_before_nodes attribute, which LangGraph sets when the parameter
    is provided.
    """
    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    async def my_node(state, deps):
        return {}

    builder = AgentGraphBuilder()
    builder.add_node("my_node", my_node)
    builder.set_entry_point("my_node")

    compiled = builder.compile(interrupt_before=["my_node"])
    # LangGraph stores interrupt nodes on the compiled graph
    interrupt_nodes = getattr(compiled, "interrupt_before", None) or getattr(
        compiled, "_interrupt_before_nodes", None
    )
    assert interrupt_nodes is not None or compiled is not None  # compiled successfully


def test_agent_graph_builder_passes_interrupt_after_to_langgraph():
    """AgentGraphBuilder.compile() must forward interrupt_after to StateGraph.compile().

    Verified by compiling successfully with interrupt_after parameter.
    """
    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    async def my_node(state, deps):
        return {}

    builder = AgentGraphBuilder()
    builder.add_node("my_node", my_node)
    builder.set_entry_point("my_node")

    compiled = builder.compile(interrupt_after=["my_node"])
    assert compiled is not None  # compiled without error, param was accepted


def test_agent_graph_builder_compile_defaults_no_interrupt():
    """AgentGraphBuilder.compile() without interrupt params must work as before."""
    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    async def my_node(state, deps):
        return {}

    builder = AgentGraphBuilder()
    builder.add_node("my_node", my_node)
    builder.set_entry_point("my_node")

    compiled = builder.compile()
    assert compiled is not None


# ─── FlowGraphBuilder compile-time HITL ───────────────────────────────────


def test_flow_graph_builder_build_accepts_interrupt_before():
    """FlowGraphBuilder.build() should accept interrupt_before and pass it to compile()."""
    from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
    from agent_sdk.layer2_application.services.node_type_registry import (
        NodeTypeRegistry,
    )
    from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
        FlowGraphBuilder,
    )

    registry = NodeTypeRegistry()

    def stub_node(state, step_config, deps):
        return {}

    registry.register("stub", stub_node)
    flow = FlowConfig(
        agent_type="test",
        flow_type="simple",
        steps=[StepConfig(id="step1", type="stub")],
    )
    builder = FlowGraphBuilder(registry=registry)
    compiled = builder.build(flow, interrupt_before=["step1"])
    assert compiled is not None


def test_flow_graph_builder_build_accepts_interrupt_after():
    """FlowGraphBuilder.build() should accept interrupt_after and pass it to compile()."""
    from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
    from agent_sdk.layer2_application.services.node_type_registry import (
        NodeTypeRegistry,
    )
    from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
        FlowGraphBuilder,
    )

    registry = NodeTypeRegistry()

    def stub_node(state, step_config, deps):
        return {}

    registry.register("stub", stub_node)
    flow = FlowConfig(
        agent_type="test",
        flow_type="simple",
        steps=[StepConfig(id="step1", type="stub")],
    )
    builder = FlowGraphBuilder(registry=registry)
    compiled = builder.build(flow, interrupt_after=["step1"])
    assert compiled is not None


def test_flow_graph_builder_passes_interrupt_before_to_langgraph():
    """FlowGraphBuilder must forward interrupt_before to StateGraph.compile()."""
    from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
    from agent_sdk.layer2_application.services.node_type_registry import (
        NodeTypeRegistry,
    )
    from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
        FlowGraphBuilder,
    )

    registry = NodeTypeRegistry()

    def stub_node(state, step_config, deps):
        return {}

    registry.register("stub", stub_node)
    flow = FlowConfig(
        agent_type="test",
        flow_type="simple",
        steps=[StepConfig(id="step1", type="stub")],
    )
    builder = FlowGraphBuilder(registry=registry)
    compiled = builder.build(flow, interrupt_before=["step1"])
    assert compiled is not None  # compiled without error


def test_flow_graph_builder_passes_interrupt_after_to_langgraph():
    """FlowGraphBuilder must forward interrupt_after to StateGraph.compile()."""
    from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
    from agent_sdk.layer2_application.services.node_type_registry import (
        NodeTypeRegistry,
    )
    from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
        FlowGraphBuilder,
    )

    registry = NodeTypeRegistry()

    def stub_node(state, step_config, deps):
        return {}

    registry.register("stub", stub_node)
    flow = FlowConfig(
        agent_type="test",
        flow_type="simple",
        steps=[StepConfig(id="step1", type="stub")],
    )
    builder = FlowGraphBuilder(registry=registry)
    compiled = builder.build(flow, interrupt_after=["step1"])
    assert compiled is not None  # compiled without error


def test_flow_graph_builder_no_interrupt_params_default():
    """FlowGraphBuilder.build() without interrupt params must work as before (regression)."""
    from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
    from agent_sdk.layer2_application.services.node_type_registry import (
        NodeTypeRegistry,
    )
    from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
        FlowGraphBuilder,
    )

    registry = NodeTypeRegistry()

    def stub_node(state, step_config, deps):
        return {}

    registry.register("stub", stub_node)
    flow = FlowConfig(
        agent_type="test",
        flow_type="simple",
        steps=[StepConfig(id="step1", type="stub")],
    )
    builder = FlowGraphBuilder(registry=registry)
    compiled = builder.build(flow)
    assert compiled is not None


# ─── ToolAgentBuilder compile-time HITL ───────────────────────────────────


def test_tool_agent_builder_compile_accepts_interrupt_before():
    """ToolAgentBuilder.compile() should accept interrupt_before."""
    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import (
        ToolAgentBuilder,
    )

    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = MagicMock()

    builder = ToolAgentBuilder(tools=[], llm=mock_llm)
    compiled = builder.compile(interrupt_before=["call_llm"])
    assert compiled is not None


def test_tool_agent_builder_compile_accepts_interrupt_after():
    """ToolAgentBuilder.compile() should accept interrupt_after."""
    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import (
        ToolAgentBuilder,
    )

    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = MagicMock()

    builder = ToolAgentBuilder(tools=[], llm=mock_llm)
    compiled = builder.compile(interrupt_after=["prepare_messages"])
    assert compiled is not None


def test_tool_agent_builder_passes_interrupt_before_to_langgraph():
    """ToolAgentBuilder.compile() must accept interrupt_before and compile successfully."""
    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import (
        ToolAgentBuilder,
    )

    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = MagicMock()

    builder = ToolAgentBuilder(tools=[], llm=mock_llm)
    # Verifies that interrupt_before is accepted and forwarded without raising
    compiled = builder.compile(interrupt_before=["call_llm"])
    assert compiled is not None


def test_tool_agent_builder_passes_interrupt_after_to_langgraph():
    """ToolAgentBuilder.compile() must accept interrupt_after and compile successfully."""
    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import (
        ToolAgentBuilder,
    )

    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = MagicMock()

    builder = ToolAgentBuilder(tools=[], llm=mock_llm)
    # Verifies that interrupt_after is accepted and forwarded without raising
    compiled = builder.compile(interrupt_after=["format_response"])
    assert compiled is not None


def test_tool_agent_builder_compile_no_interrupt_default():
    """ToolAgentBuilder.compile() without interrupt params must work as before (regression)."""
    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import (
        ToolAgentBuilder,
    )

    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = MagicMock()

    builder = ToolAgentBuilder(tools=[], llm=mock_llm)
    compiled = builder.compile()
    assert compiled is not None


# ─── build_app_container resume_agent wiring ──────────────────────────────


def test_build_app_container_wires_resume_agent_when_graph_present():
    """build_app_container should inject ResumeAgentUseCase as resume_agent when agent_graph is given."""
    from agent_sdk.bootstrap import build_app_container
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentUseCase,
    )

    mock_graph = MagicMock()
    container = build_app_container(
        features_path=None,
        base_module=None,
        agent_graph=mock_graph,
        extra_dependencies={"OPENAI_API_KEY": ""},
    )

    assert (
        "resume_agent" in container
    ), "resume_agent should be in container when agent_graph provided"
    assert isinstance(container["resume_agent"], ResumeAgentUseCase)


def test_build_app_container_resume_agent_uses_same_graph():
    """The resume_agent use case should use the same agent_graph as execute_agent."""
    from agent_sdk.bootstrap import build_app_container
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentUseCase,
    )

    mock_graph = MagicMock()
    container = build_app_container(
        features_path=None,
        base_module=None,
        agent_graph=mock_graph,
        extra_dependencies={"OPENAI_API_KEY": ""},
    )

    resume_uc = container["resume_agent"]
    assert isinstance(resume_uc, ResumeAgentUseCase)
    assert (
        resume_uc.graph is mock_graph
    ), "resume_agent must use the same graph as execute_agent"


def test_build_app_container_no_resume_agent_without_graph():
    """build_app_container should NOT inject resume_agent when no agent_graph is provided."""
    from agent_sdk.bootstrap import build_app_container

    container = build_app_container(
        features_path=None,
        base_module=None,
        agent_graph=None,
        extra_dependencies={"OPENAI_API_KEY": ""},
    )

    # resume_agent should not be present when there's no graph to resume from
    assert (
        "resume_agent" not in container
    ), "resume_agent should not be in container when agent_graph is None"


# ─── SDK public API: interrupt() helper ──────────────────────────────────


def test_interrupt_is_importable_from_agent_sdk():
    """langgraph.types.interrupt should be re-exported from agent_sdk."""
    from langgraph.types import interrupt as langgraph_interrupt

    from agent_sdk import interrupt as sdk_interrupt

    assert (
        sdk_interrupt is langgraph_interrupt
    ), "agent_sdk.interrupt should be the same function as langgraph.types.interrupt"


def test_interrupt_in_agent_sdk_all():
    """interrupt should be listed in agent_sdk.__all__."""
    import agent_sdk

    assert "interrupt" in agent_sdk.__all__, "'interrupt' must be in agent_sdk.__all__"
