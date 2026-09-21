from app.layer2_application.graphs import register_orchestration_flow


class _FakeBuilder:
    def __init__(self):
        self.nodes = []
        self.edges = []
        self.conditional_edges = []
        self.entry_point = None

    def add_node(self, name: str, fn):
        self.nodes.append((name, fn))

    def add_edge(self, source: str, target: str):
        self.edges.append((source, target))

    def set_entry_point(self, name: str):
        self.entry_point = name

    def add_conditional_edges(self, from_node: str, router_fn, path_map: dict[str, str]):
        self.conditional_edges.append((from_node, router_fn, path_map))


def test_register_orchestration_flow_sets_expected_nodes_and_routes():
    builder = _FakeBuilder()

    result = register_orchestration_flow(builder)

    assert result is builder
    assert [name for name, _ in builder.nodes] == ["main_node"]
    assert builder.entry_point == "main_node"
    assert builder.edges == [("main_node", "__end__")]
    assert builder.conditional_edges == []
