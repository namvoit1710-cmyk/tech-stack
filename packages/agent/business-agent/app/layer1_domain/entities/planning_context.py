from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from agent_sdk.layer1_domain.entities.agent_shared_state import GlobalContext, Task, Metadata, MessageHistory
from app.layer1_domain.entities.agent_hub import (
    AgentHubDTO,
    ToolHubDTO,
    WorkflowHubDTO,
)
from app.layer1_domain.entities.rag_context import RagCandidate
from app.layer1_domain.entities.workflow_node import SchemaField
from app.layer1_domain.entities.workflow_entity import WorkNodeStatus
from app.layer1_domain.entities.planner import PatchInputNodeResult


# -------------------------
# Enum
# -------------------------

class PlanningContextType(str, Enum):
    LLM = "llm"
    RULE = "rule"
    LLM_WITH_SIMPLE_NODE = "llm_with_simple_node"
    LLM_WITH_PATCH_INPUT_NODE = "llm_with_patch_input_node"
    LLM_WITH_SUB_USER_CHAT = "llm_with_sub_user_chat"
    LLM_SUMMARY_CONTEXT_GENERATOR = "llm_summary_context_generator"
    LLM_EXECUTION_SUMMARY = "llm_execution_summary"


# -------------------------
# UserIntent
# -------------------------

@dataclass
class UserIntent:
    message: str
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserIntent":
        return cls(
            message=data["message"],
            payload=data.get("payload", {}),
        )


# -------------------------
# AgentMemoryDTO (SA-1436) — recalled long-term memory passed from the orchestrator.
# Non-authoritative planning context: never overrides the current request or schema.
# -------------------------

@dataclass(slots=True)
class AgentMemoryDTO:
    memory_id: str
    memory_type: str
    scope: str
    content: str
    score: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentMemoryDTO":
        return cls(
            memory_id=str(data.get("memory_id", "")),
            memory_type=str(data.get("memory_type", "")),
            scope=str(data.get("scope", "")),
            content=str(data.get("content", "")),
            score=float(data.get("score", 0.0) or 0.0),
        )


# -------------------------
# BasePlanningContext
# -------------------------

@dataclass
class BasePlanningContext:
    workflows_hub: list[WorkflowHubDTO] = field(default_factory=list)

    @classmethod
    def from_dict_base(cls, data: dict[str, Any]):
        return {
            "workflows_hub": data.get("workflows_hub", []),
        }


# -------------------------
# PlanningContext
# -------------------------

@dataclass(kw_only=True)
class PlanningContext(BasePlanningContext):
    business_intent: str
    global_context: GlobalContext
    current_task : Task
    metadata : Metadata
    

    tools: list[ToolHubDTO] = field(default_factory=list)
    agents: list[AgentHubDTO] = field(default_factory=list)
    rag_candidates: list[RagCandidate] = field(default_factory=list)
    long_term_memories: list[AgentMemoryDTO] = field(default_factory=list)


# -------------------------
# SubUserChatPlanningContext
# -------------------------

@dataclass(kw_only=True)
class SubUserChatPlanningContext(BasePlanningContext):
    conversation_context: UserIntent
    business_intent: str
    node_info: WorkNodeStatus | None = None

    tools: list[ToolHubDTO] = field(default_factory=list)
    agents: list[AgentHubDTO] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubUserChatPlanningContext":
        base = BasePlanningContext.from_dict_base(data)

        return cls(
            conversation_context=UserIntent.from_dict(data["conversation_context"]),
            business_intent=data["business_intent"],
            node_info=data.get("node_info"),
            workflows_hub=base["workflows_hub"],
            tools=data.get("tools", []),
            agents=data.get("agents", []),
        )


# -------------------------
# SimpleNodePlanningContext
# -------------------------

@dataclass(kw_only=True)
class SimpleNodePlanningContext(BasePlanningContext):
    node_message: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SimpleNodePlanningContext":
        base = BasePlanningContext.from_dict_base(data)

        return cls(
            node_message=data["node_message"],
            workflows_hub=base["workflows_hub"],
        )


# -------------------------
# PatchInputNodePlanningContext
# -------------------------

@dataclass(kw_only=True)
class PatchInputNodePlanningContext(BasePlanningContext):
    schema: list[SchemaField]
    global_context: GlobalContext
    instruction: str
    rag_candidates: list[RagCandidate] = field(default_factory=list)
    old_patch_input_node_result: Optional[PatchInputNodeResult] = None
    executions: list[Task] = field(default_factory=list)
    
# -------------------------
# SummaryContext
# -------------------------

@dataclass(kw_only=True)
class SummaryContext(BasePlanningContext):
    context: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SummaryContext":

        return cls(
            context=data["context"],
        )


# -------------------------
# ExecutionSummaryContext
# -------------------------

@dataclass(kw_only=True)
class ExecutionSummaryContext(BasePlanningContext):
    current_message: MessageHistory
    executions: list[Task]
    global_context: GlobalContext
    metadata: Metadata