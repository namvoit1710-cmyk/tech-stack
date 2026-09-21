from __future__ import annotations

from agent_sdk import tool


@tool("ask_for_clarify", internal=True)
async def ask_for_clarify(question: str, missing: list | None = None) -> dict:
    """Ask the user ONE focused clarifying question.

    Use this only when the request is missing information needed to design the workflow
    (e.g. the trigger, a required step, or a node parameter). Ask for exactly what is
    missing — do not guess and do not design a partial workflow.

    Args:
        question: The single question to put to the user.
        missing: Optional list of the specific fields still needed.
    Returns:
        {"status": "needs_clarification", "question": str, "missing_fields": list}
    """
    return {
        "status": "needs_clarification",
        "question": question,
        "missing_fields": missing or [],
    }
