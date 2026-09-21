from __future__ import annotations

import asyncio
import inspect

from app.layer2_application.features.execute_agent.nodes.plan_execute_nodes import (
    build_material_step_records,
)


def build_graph(deps: dict):
    builder = deps.get("agent_graph_builder")
    format_response_node = deps.get("format_response_node")

    def _node(fn):
        if inspect.iscoroutinefunction(fn):
            def wrapped(state):
                return asyncio.run(fn(state, deps))
        else:
            def wrapped(state):
                return fn(state, deps)
        wrapped.__name__ = fn.__name__
        return wrapped

    builder.add_node("build_material_step_records", _node(build_material_step_records))
    builder.add_node("format_response", _node(format_response_node))
    builder.set_entry_point("build_material_step_records")
    builder.add_edge("build_material_step_records", "format_response")
    return builder.compile()

