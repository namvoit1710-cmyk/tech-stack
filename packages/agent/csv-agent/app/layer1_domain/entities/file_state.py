from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from agent_sdk.layer1_domain.entities.agent_state import AgentBaseState


@dataclass
class ConversationTurn:
    role: str
    message: str
    file_id: str | None = None


class FileState(AgentBaseState, total=False):
    """
    CSV Agent state extending AgentBaseState.

    Inherits base fields from AgentBaseState and adds CSV-specific fields.
    """

    agent_result: Dict[str, Any]
    tool_catalog: list[dict[str, Any]]
    execution_plan: dict[str, Any]
    conversation_history: list[ConversationTurn]
    execution_result: dict[str, Any]
    is_reviewed: bool
    review_iteration: int
    max_review_iterations: int
