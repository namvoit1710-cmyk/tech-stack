from __future__ import annotations

from agent_sdk import tool


@tool("plan_workflow", internal=True)
async def plan_workflow(
    nodes: list | None = None,
    rationale: str = "",
    critique: str = "",
) -> dict:
    """Record your FINALIZED node plan before building the payload.

    Call this once, after you have (1) deep-thought the request, (2) picked candidate
    nodes from list_node_types, and (3) adversarially critiqued that list. Pass the final
    node list you intend to build, why each node is there, and the critique that led you to
    it. The designer logs this plan and later self-reviews your create/update payload
    against it, so the payload has no missing or extra nodes.

    Args:
        nodes: The finalized node list; each {"id","type","name"} (ids must match the
            ids you will use in create_workflow / update_workflow).
        rationale: One or two sentences on why this set of nodes realizes the request.
        critique: What you reconsidered (a node you dropped/added, a type you corrected).
    Returns:
        {"status": "planned", "nodes": list, "rationale": str, "critique": str}
    """
    return {
        "status": "planned",
        "nodes": nodes or [],
        "rationale": rationale,
        "critique": critique,
    }
