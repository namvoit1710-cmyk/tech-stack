from __future__ import annotations

from typing import Any

from app.layer2_application.enums.graph_node_enum import GraphNode
from app.layer2_application.graphs.edges.review_outcome import make_route_after_review
from app.layer2_application.graphs.nodes.execute_node import execute_node
from app.layer2_application.graphs.nodes.plan_node import plan_node
from app.layer2_application.graphs.nodes.review_node import review_node

_DEFAULT_MAX_REVIEW_ITERATIONS = 3


def _coerce_positive_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        parsed = int(value)
    elif isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        parsed = int(value)
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return default
    else:
        return default
    return parsed if parsed > 0 else default


def _resolve_default_max_review_iterations(deps: dict[str, Any] | None) -> int:
    deps = deps or {}

    settings_provider = deps.get("settings_provider")
    getter = getattr(settings_provider, "get_default_max_review_iterations", None)
    if callable(getter):
        try:
            value = int(getter())
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass

    settings = deps.get("settings")
    return _coerce_positive_int(
        getattr(settings, "DEFAULT_MAX_REVIEW_ITERATIONS", None),
        default=_DEFAULT_MAX_REVIEW_ITERATIONS,
    )


def register_main_flow(graph: Any, deps: dict[str, Any] | None = None) -> Any:
    """Register the main CSV Agent workflow: Plan -> Execute -> Review.

    Graph topology is a framework concern, so this registration lives in layer 4
    beside the concrete SDK graph builder. The node implementations are still
    imported from the application layer and call use cases through dependencies.
    """

    graph.add_node(GraphNode.PLAN.value, plan_node)
    graph.add_node(GraphNode.EXECUTE.value, execute_node)
    graph.add_node(GraphNode.REVIEW.value, review_node)

    graph.set_entry_point(GraphNode.PLAN.value)

    graph.add_edge(GraphNode.PLAN.value, GraphNode.EXECUTE.value)
    graph.add_edge(GraphNode.EXECUTE.value, GraphNode.REVIEW.value)

    graph.add_conditional_edges(
        GraphNode.REVIEW.value,
        make_route_after_review(_resolve_default_max_review_iterations(deps)),
        {
            GraphNode.PLAN.value: GraphNode.PLAN.value,
            "__end__": "__end__",
        },
    )

    return graph
