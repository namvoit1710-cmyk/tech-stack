"""Orchestrate controller for the orchestrator service."""

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from fastapi import APIRouter, Body, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse

from app.layer2_application.features.orchestrate.use_cases.dispatch_to_agent_use_case import DispatchToAgentInput
from app.layer2_application.features.orchestrate.use_cases.get_dispatch_result_use_case import GetDispatchResultInput
from app.layer2_application.features.orchestrate.use_cases.orchestrate_use_case import OrchestrateInput
from app.layer2_application.features.orchestrate.use_cases.stop_conversation_use_case import StopConversationInput
from app.layer2_application.features.orchestrate.use_cases.agent_action_use_case import (
    AgentActionInput,
    WfInfo,
)
from app.layer3_adapters.controllers.restful.v1.authentication import require_bearer_token
from app.layer2_application.constants.ui_html import AGENT_DISPATCH_UI_HTML
from app.layer1_domain.entities.exceptions import RequestValidationAppError
from .usecase_resolver import resolve_usecase


logger = logging.getLogger(__name__)


# --- DTOs ---
@dataclass(slots=True)
class OrchestrationRequestBody:
    message: str
    conv_id: str = ""
    # SA-2114/SA-2168: task routing discriminator. Absent/empty (default) uses
    # existing LLM routing; "AGENT" routes directly to the business agent named
    # by parameters.agent_id; "RENAME" sets the conversation title from message.
    type: str = ""
    parameters: dict[str, Any] = field(
        default_factory=dict,
        metadata={"examples": [{"agent_id": "agent_123"}, {"uploaded_file_ids": ["file_id_123", "file_id_456"], "run_id": "run-123"}]},
    )

@dataclass(slots=True)
class DispatchToAgentRequestBody:
    message: str
    conv_id: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class SubConversationWfInfoBody:
    run_id: str
    node_id: str
    task_id: str | None = None

@dataclass(slots=True)
class StopConversationRequestBody:
    conv_id: str = ""

@dataclass(slots=True)
class AgentActionRequestBody:
    wf_info: SubConversationWfInfoBody | None = None
    action: str = "save"
    values: dict[str, Any] = field(default_factory=dict)
    button_label: str | None = None
    form_data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def _build_orchestrate_input(body: OrchestrationRequestBody, token_payload: dict, request: Request) -> OrchestrateInput:
    authorization_header = request.headers.get("authorization", "")
    request_context = {"auth": {"authorization": authorization_header}} if authorization_header else {}
    return OrchestrateInput(
        message=body.message,
        conv_id=body.conv_id,
        user_id=token_payload.get("user_uuid", ""),
        user_email=token_payload.get("email", ""),
        tenant_id=token_payload.get("tenant_id", ""),
        # SA-2114: routing discriminator kept off the free-form parameters dict;
        # agent_id itself now rides inside parameters (parameters.agent_id).
        type=body.type,
        parameters=body.parameters,
        context=request_context,
    )


def _build_dispatch_to_agent_input(
    body: DispatchToAgentRequestBody,
    token_payload: dict,
    request: Request,
) -> DispatchToAgentInput:
    authorization_header = request.headers.get("authorization", "")
    request_context = {"auth": {"authorization": authorization_header}} if authorization_header else {}
    return DispatchToAgentInput(
        message=body.message,
        conv_id=body.conv_id,
        user_id=token_payload.get("user_uuid", "") or token_payload.get("sub", ""),
        user_email=token_payload.get("email", ""),
        tenant_id=token_payload.get("tenant_id", "default"),
        parameters=body.parameters,
        context=request_context,
    )


def _build_agent_action_input(
    body: AgentActionRequestBody,
) -> AgentActionInput:
    wf_info = None
    if body.wf_info is not None:
        wf_info = WfInfo(
            run_id=body.wf_info.run_id,
            node_id=body.wf_info.node_id,
            task_id=body.wf_info.task_id,
        )

    return AgentActionInput(
        wf_info=wf_info,
        action=body.action,
        values=body.values,
        button_label=body.button_label,
        form_data=body.form_data,
        metadata=body.metadata,
    )


# --- Dependency Getters ---

def get_orchestrate_usecase(request: Request):
    return resolve_usecase(request, "orchestrate.orchestrate")


def get_open_workspace_usecase(request: Request):
    return resolve_usecase(request, "orchestrate.open_workspace")



def get_dispatch_to_agent_usecase(request: Request):
    return resolve_usecase(request, "orchestrate.dispatch_to_agent")


def get_available_agents_usecase(request: Request):
    return resolve_usecase(request, "orchestrate.get_available_agents")


def get_dispatch_result_usecase(request: Request):
    return resolve_usecase(request, "orchestrate.get_dispatch_result")


def get_agent_action_usecase(request: Request):
    return resolve_usecase(request, "orchestrate.agent_action")


def get_stop_usecase(request: Request):
    return resolve_usecase(request, "orchestrate.stop_conversation")



# --- Router ---

orchestrate_router = APIRouter(tags=["orchestrate"])


# --- Routes ---

@orchestrate_router.post("/async", status_code=202)
async def orchestrate_async(
    request: Request,
    body: OrchestrationRequestBody,
    token_payload: dict = Depends(require_bearer_token),
    usecase=Depends(get_orchestrate_usecase),
):
    try:
        result = await usecase.execute_fire_and_forget(
            _build_orchestrate_input(body, token_payload, request)
        )
    except RequestValidationAppError as error:
        raise HTTPException(status_code=422, detail=error.user_message)
    return asdict(result)


@orchestrate_router.post("/dispatch-to-agent")
async def dispatch_to_agent(
    request: Request,
    body: DispatchToAgentRequestBody,
    token_payload: dict = Depends(require_bearer_token),
    usecase=Depends(get_dispatch_to_agent_usecase),
):
    result = await usecase.execute(
        _build_dispatch_to_agent_input(body, token_payload, request)
    )
    if result.error:
        raise HTTPException(status_code=500, detail=result.error)
    return asdict(result)


@orchestrate_router.post("/agent-action", status_code=202)
async def agent_action(
    body: AgentActionRequestBody,
    token_payload: dict = Depends(require_bearer_token),
    usecase=Depends(get_agent_action_usecase),
):
    del token_payload
    result = await usecase.execute_fire_and_forget(
        _build_agent_action_input(body)
    )
    return asdict(result)


@orchestrate_router.post("/stop", status_code=202)
async def orchestrate_stop(
    body: StopConversationRequestBody,
    token_payload: dict = Depends(require_bearer_token),
    usecase=Depends(get_stop_usecase),
):
    # SA-892 (N5): stop BOTH halves (agent + all in-flight runs) for a conversation.
    # SA-892 (L2 authz): a valid bearer token is not enough — carry the caller's
    # tenant (+ user id/email for audit) into the use case so it can refuse a
    # cross-tenant stop (TENANT_BLEED). Identity is extracted the SAME way the
    # sibling dispatch route does (see _build_dispatch_to_agent_input).
    result = await usecase.execute(
        StopConversationInput(
            conv_id=body.conv_id,
            caller_tenant_id=token_payload.get("tenant_id", "default"),
            caller_user_id=token_payload.get("user_uuid", "")
            or token_payload.get("sub", ""),
            caller_email=token_payload.get("email", ""),
        )
    )
    if result.forbidden:
        # SA-892 (L-new2): return a fixed reason — never surface an internal
        # error string (e.g. "tenant lookup failed: ...") to the caller. The
        # detailed errors remain in the use case log / run record.
        raise HTTPException(
            status_code=403,
            detail="forbidden: conversation belongs to another tenant",
        )
    return asdict(result)


@orchestrate_router.get("/dispatch-to-agent/ui", response_class=HTMLResponse)
async def dispatch_to_agent_ui():
    return HTMLResponse(content=AGENT_DISPATCH_UI_HTML)


@orchestrate_router.get("/available-agents")
async def get_available_agents(
    usecase=Depends(get_available_agents_usecase),
):
    result = await usecase.execute()
    return asdict(result)


@orchestrate_router.get("/dispatch-results/{conversation_id}")
async def get_dispatch_result(
    conv_id: str,
    usecase=Depends(get_dispatch_result_usecase),
):
    result = usecase.execute(GetDispatchResultInput(conv_id=conv_id))
    if result.error:
        raise HTTPException(status_code=404, detail=result.error)
    return asdict(result)

