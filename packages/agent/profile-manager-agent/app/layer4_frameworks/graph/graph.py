from __future__ import annotations

import inspect

from app.layer4_frameworks.graph.nodes.profile_manager_node import profile_manager_node


def build_profile_manager_graph(deps: dict):
    builder = deps.get("agent_graph_builder")
    format_response_node = deps.get("format_response_node")

    def _get_token_from_config(config):
        if config is None:
            return None
        configurable = config.get("configurable", {})
        return configurable.get("token")

    def _node(fn):
        if inspect.iscoroutinefunction(fn):
            async def wrapped(state, config=None):  # type: ignore
                token = _get_token_from_config(config)
                return await fn(state, {**deps, "token": token})
        else:
            def wrapped(state, config=None):
                token = _get_token_from_config(config)
                return fn(state, {**deps, "token": token})
        wrapped.__name__ = fn.__name__
        return wrapped

    builder.add_node('profile_manager_node', _node(profile_manager_node))
    builder.add_node('format_response', _node(format_response_node))
    builder.set_entry_point('profile_manager_node')
    builder.add_edge('profile_manager_node', 'format_response')
    return builder.compile()
