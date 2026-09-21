from __future__ import annotations

import asyncio
import inspect

from app.layer2_application.features.execute_agent.nodes.plan_execute_nodes import (
    create_plan_node,
    execute_plan_node,
)


def build_graph(deps: dict):
    builder = deps.get("agent_graph_builder")
    format_response_node = deps.get("format_response_node")
    checkpointer = deps.get("checkpointer")

    def _node(fn):
        if inspect.iscoroutinefunction(fn):
            def wrapped(state):
                return asyncio.run(fn(state, deps))
        else:
            def wrapped(state):
                return fn(state, deps)
        wrapped.__name__ = fn.__name__
        return wrapped

    builder.add_node("create_plan", _node(create_plan_node))
    builder.add_node("execute_plan", _node(execute_plan_node))
    builder.add_node("format_response", _node(format_response_node))
    builder.set_entry_point("create_plan")
    builder.add_edge("create_plan", "execute_plan")
    builder.add_edge("execute_plan", "format_response")
    return builder.compile(checkpointer=checkpointer)

