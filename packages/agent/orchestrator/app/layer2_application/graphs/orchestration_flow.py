"""Application-owned orchestration graph registration."""

from __future__ import annotations

import inspect

from app.layer2_application.interfaces.graph_builder_interface import IGraphBuilder
from app.layer2_application.graphs.nodes.main_node import main_node


def register_orchestration_flow(graph: IGraphBuilder,) -> IGraphBuilder:
    graph.add_node("main_node", main_node)
    graph.set_entry_point("main_node")
    graph.add_edge("main_node", "__end__")
    return graph
