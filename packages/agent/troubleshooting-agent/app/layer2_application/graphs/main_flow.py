from typing import Any

from app.layer2_application.enums.graph_node_enum import GraphNode
from app.layer2_application.graphs.nodes.collect_node import collect_node
from app.layer2_application.graphs.nodes.diagnose_node import diagnose_node
from app.layer2_application.graphs.nodes.report_node import report_node
from app.layer2_application.interfaces.graph_builder_interface import IGraphBuilder


def register_main_flow(graph: IGraphBuilder) -> Any:
    graph.add_node(GraphNode.COLLECT.value, collect_node)
    graph.add_node(GraphNode.DIAGNOSE.value, diagnose_node)
    graph.add_node(GraphNode.REPORT.value, report_node)
    graph.set_entry_point(GraphNode.COLLECT.value)
    graph.add_edge(GraphNode.COLLECT.value, GraphNode.DIAGNOSE.value)
    graph.add_edge(GraphNode.DIAGNOSE.value, GraphNode.REPORT.value)
    graph.add_edge(GraphNode.REPORT.value, "__end__")
    return graph
