"""Interprets the routing verdict from OrchestrationPlan — no LLM call."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from app.layer1_domain.entities.agent.scored_agent import ScoredAgent
from app.layer1_domain.entities.planner.task_breakdown_response import OrchestrationPlan


class AmbiguityStatus(str, Enum):
    UNAMBIGUOUS = "unambiguous"
    AMBIGUOUS = "ambiguous"
    NO_MATCH = "no_match"
    DIRECT_ANSWER = "direct_answer"


@dataclass
class AgentOption:
    index: int
    agent_id: str
    label: str
    description: str


@dataclass
class AmbiguityEvaluation:
    status: AmbiguityStatus
    selected: Optional[ScoredAgent] = None
    candidates: list[ScoredAgent] = field(default_factory=list)
    options: list[AgentOption] = field(default_factory=list)
    clarification_message: str = ""
    direct_message: str = ""


class AmbiguityDetector:
    """Reads the routing verdict from OrchestrationPlan — pure logic, no LLM call."""

    def evaluate(
        self,
        plan: OrchestrationPlan,
        agents: list[ScoredAgent],
    ) -> AmbiguityEvaluation:
        try:
            status = AmbiguityStatus(plan.status)
        except ValueError:
            status = AmbiguityStatus.NO_MATCH

        if status == AmbiguityStatus.NO_MATCH:
            return AmbiguityEvaluation(status=status)

        if status == AmbiguityStatus.DIRECT_ANSWER:
            # Conversational / meta turn (greeting, thanks, "what can you do?"). No agent runs;
            # the orchestrator replies with the plan's direct_message.
            return AmbiguityEvaluation(status=status, direct_message=plan.direct_message)

        if status == AmbiguityStatus.UNAMBIGUOUS:
            selected = next(
                (a for a in agents if a.id == plan.selected_agent_id),
                agents[0] if agents else None,
            )
            return AmbiguityEvaluation(
                status=status,
                selected=selected,
                candidates=agents,
            )

        # AMBIGUOUS — surface candidate agents in the order the LLM listed them
        ordered = [a for aid in plan.candidate_agent_ids for a in agents if a.id == aid]
        candidates = ordered or agents
        return AmbiguityEvaluation(
            status=status,
            candidates=candidates,
            clarification_message=plan.clarification_question,
        )
