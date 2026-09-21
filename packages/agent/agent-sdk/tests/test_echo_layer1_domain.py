import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "examples", "echo_agent")
)


def test_echo_state_importable_from_layer1_domain():
    from app.layer1_domain.echo_state import EchoState

    annotations = EchoState.__annotations__
    assert "agent_result" in annotations
    assert "confirmed" in annotations


def test_echo_state_layer1_has_no_other_layer_imports():
    import ast
    import inspect

    from app.layer1_domain.echo_state import EchoState

    source = inspect.getsource(EchoState)
    tree = ast.parse(source)
    class_def = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "EchoState"
    )
    own_annotations = {
        node.target.id
        for node in ast.walk(class_def)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert "agent_result" in own_annotations
    assert "confirmed" in own_annotations


def test_echo_state_layer1_only_imports_agent_sdk_and_stdlib():
    import ast
    import pathlib

    module_path = (
        pathlib.Path(__file__).parent.parent
        / "examples"
        / "echo_agent"
        / "app"
        / "layer1_domain"
        / "echo_state.py"
    )
    source = module_path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(
                "app.layer"
            ), f"layer1_domain must not import from other app layers, found: {node.module}"
