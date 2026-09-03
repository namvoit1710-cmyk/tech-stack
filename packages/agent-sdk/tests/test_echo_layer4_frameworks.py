import ast
import os
import pathlib
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "examples", "echo_agent")
)


def test_build_echo_graph_importable_from_layer4_frameworks():
    from app.layer4_frameworks.graph.echo_graph_builder import build_echo_graph

    assert callable(build_echo_graph)


def test_build_echo_graph_returns_compiled_graph():
    from app.layer4_frameworks.graph.echo_graph_builder import build_echo_graph

    graph = build_echo_graph()
    assert graph is not None


def test_build_echo_graph_uses_direct_completion_topology():
    from app.layer4_frameworks.graph.echo_graph_builder import build_echo_graph

    graph = build_echo_graph()
    topology = graph.get_graph()
    edges = {(edge.source, edge.target) for edge in topology.edges}

    assert "openai" in graph.nodes
    assert "hitl" in graph.nodes
    assert "tools" not in graph.nodes
    assert ("__start__", "openai") in edges
    assert ("openai", "__end__") in edges
    assert ("openai", "hitl") not in edges
    assert ("hitl", "__end__") not in edges


def test_echo_graph_builder_imports_echo_state_from_layer1_domain():
    module_path = (
        pathlib.Path(__file__).parent.parent
        / "examples"
        / "echo_agent"
        / "app"
        / "layer4_frameworks"
        / "graph"
        / "echo_graph_builder.py"
    )
    source = module_path.read_text()
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    assert (
        "app.layer1_domain.echo_state" in imports
    ), "echo_graph_builder.py must import EchoState from app.layer1_domain.echo_state"


def test_echo_graph_builder_imports_nodes_from_layer2_application():
    module_path = (
        pathlib.Path(__file__).parent.parent
        / "examples"
        / "echo_agent"
        / "app"
        / "layer4_frameworks"
        / "graph"
        / "echo_graph_builder.py"
    )
    source = module_path.read_text()
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    assert (
        "app.layer2_application.echo_nodes" in imports
    ), "echo_graph_builder.py must import nodes from app.layer2_application.echo_nodes"


def test_echo_graph_builder_does_not_import_from_layer3():
    module_path = (
        pathlib.Path(__file__).parent.parent
        / "examples"
        / "echo_agent"
        / "app"
        / "layer4_frameworks"
        / "graph"
        / "echo_graph_builder.py"
    )
    source = module_path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(
                "app.layer3"
            ), f"echo_graph_builder.py must not import from layer3, found: {node.module}"
