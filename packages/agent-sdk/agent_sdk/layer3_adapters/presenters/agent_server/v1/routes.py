import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agent_sdk.layer1_domain.entities.agent_request import RequestContext
from agent_sdk.layer1_domain.entities.conversation_metadata import ConversationMetadata
from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
    ExecuteAgentInput,
)
from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
    ResumeAgentInput,
)
from agent_sdk.layer3_adapters.presenters.agent_server.dtos.base_input import (
    BaseInputPydantic,
)
from agent_sdk.layer3_adapters.presenters.agent_server.dtos.base_output import (
    BaseOutputPydantic,
)


class ExecuteAgentInputPydantic(BaseInputPydantic):
    message: str
    conv_id: str = ""
    user_id: str = "anonymous"
    tenant_id: str = "default"
    source: str = "api"
    correlation_id: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    context: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    uploaded_file_ids: List[str] = Field(default_factory=list)
    reply_to: Optional[str] = None
    reply_topic: Optional[str] = None
    action: Optional[str] = None
    agent_type: Optional[str] = None
    intent: Dict[str, Any] = Field(default_factory=dict)
    execution_context: Dict[str, Any] = Field(default_factory=dict)
    context_snapshot: Dict[str, Any] = Field(default_factory=dict)

    def to_dataclass(self, dataclass_cls):
        data = self.model_dump()
        if data.get("context") is not None:
            data["context"] = RequestContext(**data["context"])
        if data.get("metadata") is not None:
            data["metadata"] = ConversationMetadata(**data["metadata"])
        return dataclass_cls(**data)


class HitlInterruptPayloadPydantic(BaseModel):
    thread_id: str
    interrupt_id: str
    value: Any
    type: str = "GENERIC"
    message: str = ""
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    conv_id: Optional[str] = None
    metadata: Dict[str, Any] = {}

    @classmethod
    def from_domain(cls, payload: Any) -> "HitlInterruptPayloadPydantic":
        return cls(
            thread_id=payload.thread_id,
            interrupt_id=payload.interrupt_id,
            value=payload.value,
            type=getattr(
                getattr(payload, "type", "GENERIC"),
                "value",
                getattr(payload, "type", "GENERIC"),
            ),
            message=getattr(payload, "message", ""),
            tenant_id=payload.tenant_id,
            user_id=payload.user_id,
            conv_id=payload.conv_id,
            metadata=getattr(payload, "metadata", {}),
        )


class ExecuteAgentOutputPydantic(BaseOutputPydantic):
    message: str = ""
    status: str = "success"
    session_id: str = ""
    data: Dict[str, Any] = {}
    agent_data: Dict[str, Any] = {}
    error: Optional[str] = None
    error_code: Optional[str] = None
    related_step_id: Optional[str] = None
    is_critical: bool = False
    error_context: Optional[Dict[str, Any]] = None
    correlation_id: Optional[str] = None
    duration_ms: float = 0.0
    interrupted: bool = False
    interrupt_payload: Optional[HitlInterruptPayloadPydantic] = None

    @classmethod
    def from_dataclass(cls, dataclass_obj) -> "ExecuteAgentOutputPydantic":
        data = dataclass_obj.__dict__.copy()
        if data.get("interrupt_payload") is not None:
            data["interrupt_payload"] = HitlInterruptPayloadPydantic.from_domain(
                data["interrupt_payload"]
            )
        return cls(**data)


class AgentInfoOutputPydantic(BaseOutputPydantic):
    agent_type: str
    version: str
    sdk_version: str
    domain: str
    capabilities: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}


class ResumeAgentInputPydantic(BaseInputPydantic):
    thread_id: str
    resume_value: Any
    interrupt_id: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    uploaded_file_ids: List[str] = Field(default_factory=list)
    reply_to: Optional[str] = None
    reply_topic: Optional[str] = None

    def to_dataclass(self, dataclass_cls):
        data = self.model_dump()
        if data.get("metadata") is not None:
            data["metadata"] = ConversationMetadata(**data["metadata"])
        return dataclass_cls(**data)


class ResumeAgentOutputPydantic(BaseOutputPydantic):
    message: str = ""
    status: str = "success"
    data: Dict[str, Any] = {}
    agent_data: Dict[str, Any] = {}
    error: Optional[str] = None
    error_code: Optional[str] = None
    related_step_id: Optional[str] = None
    is_critical: bool = False
    error_context: Optional[Dict[str, Any]] = None
    correlation_id: Optional[str] = None
    duration_ms: float = 0.0
    interrupted: bool = False
    interrupt_payload: Optional[HitlInterruptPayloadPydantic] = None

    @classmethod
    def from_dataclass(cls, dataclass_obj) -> "ResumeAgentOutputPydantic":
        data = dataclass_obj.__dict__.copy()
        if data.get("interrupt_payload") is not None:
            data["interrupt_payload"] = HitlInterruptPayloadPydantic.from_domain(
                data["interrupt_payload"]
            )
        return cls(**data)


def create_router(container: dict) -> APIRouter:
    _logger = logging.getLogger(__name__)
    router = APIRouter()
    if "get_agent_info" in container:
        info_uc = container["get_agent_info"]

        @router.get("/info", response_model=AgentInfoOutputPydantic)
        def info():
            domain_output = info_uc.execute()
            return AgentInfoOutputPydantic.from_dataclass(domain_output)
    else:
        _logger.warning(
            "Container missing 'get_agent_info' — /info endpoint not registered"
        )

    if "execute_agent" in container:
        exec_uc = container["execute_agent"]

        @router.post("/execute", response_model=ExecuteAgentOutputPydantic)
        async def execute(payload: ExecuteAgentInputPydantic):
            domain_input = payload.to_dataclass(ExecuteAgentInput)
            domain_output = await exec_uc.execute(domain_input)
            return ExecuteAgentOutputPydantic.from_dataclass(domain_output)
    else:
        _logger.warning(
            "Container missing 'execute_agent' — /execute endpoint not registered"
        )

    if "resume_agent" in container:
        resume_uc = container["resume_agent"]

        @router.post("/resume", response_model=ResumeAgentOutputPydantic)
        async def resume(payload: ResumeAgentInputPydantic):
            domain_input = payload.to_dataclass(ResumeAgentInput)
            domain_output = await resume_uc.execute(domain_input)
            return ResumeAgentOutputPydantic.from_dataclass(domain_output)
    else:
        _logger.warning(
            "Container missing 'resume_agent' — /resume endpoint not registered"
        )

    return router
