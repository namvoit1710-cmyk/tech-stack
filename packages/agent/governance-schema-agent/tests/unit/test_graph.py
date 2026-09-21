from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.layer4_frameworks.graph import build_graph


def test_build_graph_wires_nodes_and_compile():
    builder = MagicMock()
    builder.compile.return_value = "compiled-graph"

    def format_response_node(state, deps):
        return {"state": state, "deps": deps}

    def build_material_step_records(state, deps):
        return {"state": state, "deps": deps}

    deps = {
        "agent_graph_builder": builder,
        "format_response_node": format_response_node,
    }

    with patch(
        "app.layer4_frameworks.graph.build_material_step_records",
        build_material_step_records,
    ):
        result = build_graph(deps)

    assert result == "compiled-graph"
    assert builder.add_node.call_args_list[0][0][0] == "build_material_step_records"
    assert builder.add_node.call_args_list[1][0][0] == "format_response"
    assert builder.add_node.call_args_list[0][0][1].__name__ == "build_material_step_records"
    assert builder.add_node.call_args_list[1][0][1].__name__ == "format_response_node"
    builder.set_entry_point.assert_called_once_with("build_material_step_records")
    builder.add_edge.assert_called_once_with("build_material_step_records", "format_response")
    builder.compile.assert_called_once_with()

