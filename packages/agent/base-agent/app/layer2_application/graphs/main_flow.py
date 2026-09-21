from typing import Any

from app.layer2_application.enums.graph_node_enum import GraphNode
from app.layer2_application.graphs.edges.review_outcome import route_after_review
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.graphs.nodes.execute_node import execute_node
from app.layer2_application.graphs.nodes.plan_usecase import plan_node
from app.layer2_application.graphs.nodes.review_node import review_node
from app.layer2_application.interfaces.graph_builder_interface import IGraphBuilder

def register_main_flow(graph: IGraphBuilder) -> Any:
    graph.add_node(GraphNode.PLAN.value, plan_node)
    graph.add_node(GraphNode.EXECUTE.value, execute_node)
    graph.add_node(GraphNode.REVIEW.value, review_node)
    graph.set_entry_point(GraphNode.PLAN.value)
    graph.add_edge(GraphNode.PLAN.value, GraphNode.EXECUTE.value)
    graph.add_edge(GraphNode.EXECUTE.value, GraphNode.REVIEW.value)

    graph.add_conditional_edges(
        GraphNode.REVIEW.value,
        route_after_review,
        {
            GraphNode.PLAN.value: GraphNode.PLAN.value,
            "__end__": "__end__",
        },
    )

    return graph
