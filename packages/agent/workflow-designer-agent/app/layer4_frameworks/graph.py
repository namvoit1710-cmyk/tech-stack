from __future__ import annotations

import asyncio
import inspect
from typing import Any

from app.layer1_domain.entities.workflow_design_state import ConversationTurn


def _normalize_history(history: Any) -> list[ConversationTurn]:
    if not isinstance(history, list):
        return []
    turns: list[ConversationTurn] = []
    for item in history:
        role = getattr(item, "role", None)
        message = getattr(item, "message", None)
        if isinstance(role, str) and isinstance(message, str):
            turns.append(ConversationTurn(role=role, message=message))
    return turns


async def _design_workflow_node(state: dict, deps: dict) -> dict:
    """LangGraph adapter: run the design business flow, map its result to transport."""
    use_case = deps.get("design_workflow_use_case")
    settings = deps.get("app_settings")
    agent_type = settings.AGENT_TYPE if settings else "workflow-designer"

    message = state.get("message", "")
    history = _normalize_history(state.get("conversation_history", []))
    history.append(ConversationTurn(role="user", message=message))

    # The workflow this conversation is building (threaded via the checkpointer), so a
    # follow-up turn updates it instead of creating a new one.
    current_workflow = state.get("current_workflow") or None
    result = await use_case.design(message, history, current_workflow=current_workflow)
    history.append(ConversationTurn(role="system", message=result.content))

    if result.workflow_id:
        current_workflow = {"workflow_id": result.workflow_id, "workflow": result.workflow}

    return {
        "formatted_response": {
            "status": result.status,
            "content": result.content,
            "agent_type": agent_type,
            "agent_data": {
                "workflow": result.workflow,
                "workflow_id": result.workflow_id,
                "run_id": result.run_id,
                "link": result.link,
                "raw_result": result.raw,
            },
        },
        "workflow_definition": result.workflow,
        "current_workflow": current_workflow,
        "conversation_history": history,
    }


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

    builder.add_node("design_workflow", _node(_design_workflow_node))
    builder.add_node("format_response", _node(format_response_node))
    builder.set_entry_point("design_workflow")
    builder.add_edge("design_workflow", "format_response")
    return builder.compile(checkpointer=checkpointer)
