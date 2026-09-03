def test_graph_init_exports_echo_state():
    from app.layer1_domain.echo_state import EchoState

    assert EchoState is not None


def test_graph_init_exports_build_echo_graph():
    from app.layer4_frameworks.graph.echo_graph_builder import build_echo_graph

    assert callable(build_echo_graph)


def test_graph_init_all_contains_both_exports():
    from app.layer1_domain.echo_state import EchoState
    from app.layer4_frameworks.graph.echo_graph_builder import build_echo_graph

    assert EchoState is not None
    assert callable(build_echo_graph)
