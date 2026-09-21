def test_compiler_exports_build_echo_graph():
    from app.layer4_frameworks.graph.echo_graph_builder import (
        build_echo_graph,
    )

    assert callable(build_echo_graph)


def test_build_echo_graph_returns_compiled_graph():
    from app.layer4_frameworks.graph.echo_graph_builder import (
        build_echo_graph,
    )

    graph = build_echo_graph()
    assert graph is not None


def test_build_echo_graph_accepts_deps_and_checkpointer():
    from app.layer4_frameworks.graph.echo_graph_builder import (
        build_echo_graph,
    )

    graph = build_echo_graph(deps={}, checkpointer=None)
    assert graph is not None
