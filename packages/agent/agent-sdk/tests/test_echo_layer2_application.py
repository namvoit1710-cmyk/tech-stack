import asyncio
import os
import sys
from unittest.mock import AsyncMock, Mock

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "examples", "echo_agent")
)


def test_echo_nodes_importable_from_layer2_application():
    from app.layer2_application.echo_nodes import hitl_node, openai_node

    assert callable(openai_node)
    assert callable(hitl_node)


def test_get_current_time_importable_from_layer2_application():
    from app.layer2_application.echo_nodes import get_current_time

    assert get_current_time is not None


def test_get_current_time_returns_formatted_string():
    from app.layer2_application.echo_nodes import get_current_time

    result = get_current_time.invoke({})
    assert isinstance(result, str)
    assert len(result) == 19


def test_openai_node_echoes_without_service():
    from app.layer2_application.echo_nodes import openai_node

    state = {"message": "hello world"}
    result = asyncio.run(openai_node(state, {}))
    assert result == {"agent_result": {"content": "[echo] hello world"}}


def test_openai_node_reads_openai_service_dependency():
    from app.layer2_application.echo_nodes import openai_node

    openai_service = Mock()
    openai_service.get_chat_completion = AsyncMock(return_value="professional hello")

    result = asyncio.run(
        openai_node({"message": "hello world"}, {"openai_service": openai_service})
    )

    openai_service.get_chat_completion.assert_awaited_once()
    assert result == {"agent_result": {"content": "professional hello"}}


def test_hitl_node_returns_processing_when_confirmed():
    from app.layer2_application.echo_nodes import hitl_node

    state = {"confirmed": True}
    result = hitl_node(state, {})
    assert result == {"transport_state": "PROCESSING"}


def test_echo_nodes_uses_layer1_domain_echo_state():
    import ast
    import pathlib

    module_path = (
        pathlib.Path(__file__).parent.parent
        / "examples"
        / "echo_agent"
        / "app"
        / "layer2_application"
        / "echo_nodes.py"
    )
    source = module_path.read_text()
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    assert (
        "app.layer1_domain.echo_state" in imports
    ), "echo_nodes.py must import EchoState from app.layer1_domain.echo_state"


def test_echo_nodes_does_not_import_from_layer3_or_layer4():
    import ast
    import pathlib

    module_path = (
        pathlib.Path(__file__).parent.parent
        / "examples"
        / "echo_agent"
        / "app"
        / "layer2_application"
        / "echo_nodes.py"
    )
    source = module_path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(
                "app.layer3"
            ), f"echo_nodes.py must not import from layer3, found: {node.module}"
            assert not node.module.startswith(
                "app.layer4"
            ), f"echo_nodes.py must not import from layer4, found: {node.module}"
