from __future__ import annotations
from app.layer2_application.graphs.nodes.validation_rule_node import validation_rule_node


def build_validation_file_graph(deps: dict):
    builder = deps["agent_graph_builder"]
    format_response_node = deps["format_response_node"]
    import inspect
    def _node(fn):
        if inspect.iscoroutinefunction(fn):
            async def wrapped(state):
                return await fn(state, deps)
        else:
            def wrapped(state):
                return fn(state, deps)
        wrapped.__name__ = fn.__name__
        return wrapped
    builder.add_node('validation_rule_processor_node', _node(validation_rule_node))
    builder.add_node('format_response', _node(format_response_node))
    builder.set_entry_point('validation_rule_processor_node')
    builder.add_edge('validation_rule_processor_node', 'format_response')
    return builder.compile()
