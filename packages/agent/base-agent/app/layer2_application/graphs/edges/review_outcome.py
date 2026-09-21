from app.layer2_application.enums.graph_node_enum import GraphNode


def route_after_review(state: dict) -> str:
    is_reviewed = state.get("is_reviewed", True)
    if is_reviewed:
        return "__end__"
    return GraphNode.PLAN.value

