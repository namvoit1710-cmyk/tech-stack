
from app.layer2_application.enums.graph_node_enum import GraphNode
from app.layer2_application.graphs.nodes.react_event_node import react_event_node
from app.layer2_application.interfaces.graph_builder_interface import IGraphBuilder

def register_main_flow(graph: IGraphBuilder):
    graph.add_node(GraphNode.REACT_EVENT.value, react_event_node)
    graph.set_entry_point(GraphNode.REACT_EVENT.value)
    return graph
