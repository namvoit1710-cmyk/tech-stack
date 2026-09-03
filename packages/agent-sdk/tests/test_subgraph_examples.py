from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import TypedDict

import pytest

EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "subgraph_example.py"


def _load_subgraph_example_module():
    spec = spec_from_file_location("test_subgraph_example_module", EXAMPLE_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ParentState(TypedDict):
    foo: str


class ChildState(TypedDict):
    bar: str


class TestAddMappedSubgraphDifferentSchemas:
    async def test_add_mapped_subgraph_transforms_state_between_parent_and_child(self):
        from langgraph.graph import END, StateGraph

        from agent_sdk.layer4_frameworks.graph.agent_graph_builder import (
            AgentGraphBuilder,
        )

        sub_builder = StateGraph(ChildState)
        sub_builder.add_node(
            "child_node", lambda state: {"bar": f"mapped: {state['bar']}"}
        )
        sub_builder.add_edge("child_node", END)
        sub_builder.set_entry_point("child_node")
        subgraph = sub_builder.compile()

        builder = AgentGraphBuilder(state_schema=ParentState)
        builder.add_mapped_subgraph(
            "transformer",
            subgraph,
            map_input=lambda state: {"bar": state["foo"]},
            map_output=lambda out, state: {"foo": out["bar"]},
        )
        builder.set_entry_point("transformer")
        graph = builder.compile()

        result = await graph.ainvoke({"foo": "hello"})
        assert result["foo"] == "mapped: hello"


class TestExampleBuilders:
    def test_build_shared_state_graph_produces_correct_output(self):
        example = _load_subgraph_example_module()

        result = example.build_shared_state_graph().invoke(
            {"input": "  hello subgraph  "}
        )
        assert result["output"] == "processed: hello subgraph"

    async def test_build_mapped_subgraph_graph_produces_correct_output(self):
        example = _load_subgraph_example_module()

        result = await example.build_mapped_subgraph_graph().ainvoke(
            {"input": "  hello mapped subgraph  "}
        )
        assert result["output"] == "mapped: hello mapped subgraph"


class TestFlowGraphBuilderFailFast:
    def test_build_raises_on_missing_subgraph_ref(self):
        from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
        from agent_sdk.layer2_application.services.node_type_registry import (
            NodeTypeRegistry,
        )
        from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
            FlowGraphBuilder,
        )

        config = FlowConfig(
            agent_type="test",
            flow_type="test",
            steps=[StepConfig(id="step1", type="subgraph", subflow_ref="missing_ref")],
        )
        builder = FlowGraphBuilder(registry=NodeTypeRegistry())

        with pytest.raises(ValueError, match="missing_ref"):
            builder.build(config, subgraphs={})

    def test_build_raises_on_missing_subgraph_dict_entirely(self):
        from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
        from agent_sdk.layer2_application.services.node_type_registry import (
            NodeTypeRegistry,
        )
        from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
            FlowGraphBuilder,
        )

        config = FlowConfig(
            agent_type="test",
            flow_type="test",
            steps=[StepConfig(id="step1", type="subgraph", subflow_ref="any_ref")],
        )
        builder = FlowGraphBuilder(registry=NodeTypeRegistry())

        with pytest.raises(ValueError, match="any_ref"):
            builder.build(config, subgraphs=None)
