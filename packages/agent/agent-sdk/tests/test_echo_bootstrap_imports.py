import ast
import pathlib


def test_bootstrap_imports_build_echo_graph_from_layer4_frameworks():
    bootstrap_path = (
        pathlib.Path(__file__).parent.parent
        / "examples"
        / "echo_agent"
        / "bootstrap.py"
    )
    source = bootstrap_path.read_text()
    tree = ast.parse(source)
    layer4_import_found = False
    old_import_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "app.layer4_frameworks.graph.echo_graph_builder":
                for alias in node.names:
                    if alias.name == "build_echo_graph":
                        layer4_import_found = True
            if node.module.startswith(
                "app.layer2_application.features.execute_agent.graph"
            ):
                for alias in node.names:
                    if alias.name == "build_echo_graph":
                        old_import_found = True
    assert layer4_import_found, (
        "bootstrap.py must import build_echo_graph from "
        "app.layer4_frameworks.graph.echo_graph_builder"
    )
    assert not old_import_found, (
        "bootstrap.py must NOT import build_echo_graph from "
        "app.layer2_application.features.execute_agent.graph"
    )
