
# Minimal test for REACT_EVENT node and entry point
import importlib
from app.layer2_application.enums.graph_node_enum import GraphNode

def test_register_main_flow_react_event():
    main_flow = importlib.import_module("app.layer2_application.graphs.main_flow")
    class DummyBuilder:
        def __init__(self):
            self.nodes = []
            self.entry_point = None
        def add_node(self, name, fn):
            self.nodes.append((name, fn))
        def set_entry_point(self, name):
            self.entry_point = name
    builder = DummyBuilder()
    result = main_flow.register_main_flow(builder)
    assert result is builder
    node_names = [name for name, _ in builder.nodes]
    assert GraphNode.REACT_EVENT.value in node_names
    assert builder.entry_point == GraphNode.REACT_EVENT.value

