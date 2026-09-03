import os
import sys
from unittest.mock import patch

_ECHO_AGENT_DIR = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "examples",
    "echo_agent",
)
sys.path.insert(0, os.path.abspath(_ECHO_AGENT_DIR))


def test_hitl_node_string_approved_sets_confirmed_true():
    from app.layer2_application.echo_nodes import hitl_node

    state = {"confirmed": False, "agent_result": {"content": "hello"}}
    with patch(
        "app.layer2_application.echo_nodes.interrupt",
        return_value="approved",
    ):
        result = hitl_node(state, {})

    assert result["confirmed"] is True
    assert result["transport_state"] == "PROCESSING"


def test_hitl_node_string_rejected_sets_confirmed_false():
    from app.layer2_application.echo_nodes import hitl_node

    state = {"confirmed": False, "agent_result": {"content": "hello"}}
    with patch(
        "app.layer2_application.echo_nodes.interrupt",
        return_value="rejected",
    ):
        result = hitl_node(state, {})

    assert result["confirmed"] is False
    assert result["transport_state"] == "PROCESSING"


def test_hitl_node_dict_confirmed_true():
    from app.layer2_application.echo_nodes import hitl_node

    state = {"confirmed": False, "agent_result": {"content": "hello"}}
    with patch(
        "app.layer2_application.echo_nodes.interrupt",
        return_value={"confirmed": True},
    ):
        result = hitl_node(state, {})

    assert result["confirmed"] is True
    assert result["transport_state"] == "PROCESSING"


def test_hitl_node_dict_confirmed_false():
    from app.layer2_application.echo_nodes import hitl_node

    state = {"confirmed": False, "agent_result": {"content": "hello"}}
    with patch(
        "app.layer2_application.echo_nodes.interrupt",
        return_value={"confirmed": False},
    ):
        result = hitl_node(state, {})

    assert result["confirmed"] is False
    assert result["transport_state"] == "PROCESSING"


def test_hitl_node_dict_missing_confirmed_defaults_false():
    from app.layer2_application.echo_nodes import hitl_node

    state = {"confirmed": False, "agent_result": {"content": "hello"}}
    with patch(
        "app.layer2_application.echo_nodes.interrupt",
        return_value={},
    ):
        result = hitl_node(state, {})

    assert result["confirmed"] is False
    assert result["transport_state"] == "PROCESSING"


def test_hitl_node_already_confirmed_skips_interrupt():
    from app.layer2_application.echo_nodes import hitl_node

    state = {"confirmed": True}
    with patch(
        "app.layer2_application.echo_nodes.interrupt",
    ) as mock_interrupt:
        result = hitl_node(state, {})

    mock_interrupt.assert_not_called()
    assert result == {"transport_state": "PROCESSING"}


def test_build_echo_graph_uses_tool_agent_builder_path():
    from app.layer4_frameworks.graph.echo_graph_builder import build_echo_graph

    graph = build_echo_graph()
    topology = graph.get_graph()
    edges = {(edge.source, edge.target) for edge in topology.edges}

    assert "openai" in graph.nodes
    assert "hitl" in graph.nodes
    assert "tools" not in graph.nodes
    assert ("__start__", "openai") in edges
    # assert ("openai", "hitl") in edges
    # assert ("hitl", "__end__") in edges
    assert ("openai", "__end__") in edges
