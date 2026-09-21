from typing import Any

from app.layer2_application.enums.graph_node_enum import GraphNode
from app.layer2_application.graphs.nodes.draft_node import draft_business_agent_node
from app.layer2_application.interfaces.graph_builder_interface import IGraphBuilder


def register_main_flow(graph: IGraphBuilder) -> Any:
    graph.add_node(GraphNode.DRAFT.value, draft_business_agent_node)
    graph.set_entry_point(GraphNode.DRAFT.value)
    graph.add_edge(GraphNode.DRAFT.value, "__end__")
    return graph
