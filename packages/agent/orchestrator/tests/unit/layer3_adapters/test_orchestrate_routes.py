import httpx
import pytest
from starlette.requests import Request

import importlib.util, os
# Load the local orchestrator main.py explicitly to avoid agent-sdk's main
spec = importlib.util.spec_from_file_location("main", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "main.py")))
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)
create_app = main.create_app
from app.layer2_application.features.orchestrate.use_cases.dispatch_to_agent_use_case import (
    DispatchToAgentOutput,
)
from app.layer2_application.features.orchestrate.use_cases.get_available_agents_use_case import (
    GetAvailableAgentsOutput,
)
from app.layer2_application.features.orchestrate.use_cases.get_dispatch_result_use_case import (
    GetDispatchResultOutput,
)
from app.layer2_application.features.orchestrate.use_cases.orchestrate_use_case import (
    OrchestrateAsyncOutput,
)
from app.layer2_application.features.orchestrate.use_cases.agent_action_use_case import (
    AgentActionOutput,
)
from app.layer3_adapters.controllers.restful.v1.orchestrator_controller import (
    AgentActionRequestBody,
    DispatchToAgentRequestBody,
    OrchestrationRequestBody,
    SubConversationWfInfoBody,
    _build_agent_action_input,
    _build_dispatch_to_agent_input,
    _build_orchestrate_input,
)
from app.layer3_adapters.controllers.restful.v1.authentication import require_bearer_token


class StubOrchestrateUseCase:
    def __init__(self):
        self.last_request = None

    async def execute_fire_and_forget(self, request):
        self.last_request = request
        return OrchestrateAsyncOutput(
            message=request.message,
            conv_id=request.conv_id or "conv-1",
            uploaded_file_ids=request.parameters.get("uploaded_file_ids", []),
        )


class StubDispatchToAgentUseCase:
    def __init__(self):
        self.last_request = None

    async def execute(self, request):
        self.last_request = request
        return DispatchToAgentOutput(
            status="queued",
            conv_id=request.conv_id or "conv-1",
            correlation_id="corr-1",
            request_id="req-2",
        )


class StubGetAvailableAgentsUseCase:
    async def execute(self):
        return GetAvailableAgentsOutput(
            agents=[
                {
                    "id": "agent-1",
                    "name": "Business Agent",
                    "kind": "workflow",
                    "description": "Published business agent",
                    "status": "active",
                    "is_published": True,
                }
            ]
        )


class StubGetDispatchResultUseCase:
    def __init__(self):
        self.last_request = None

    def execute(self, request):
        self.last_request = request
        return GetDispatchResultOutput(
            conv_id=request.conv_id,
            current_task={"task_id": "task-1", "status": "pending"},
            shared_state={
                "metadata": {"conversation_id": request.conv_id},
                "executions": [{"task_id": "task-1", "status": "pending"}],
            },
        )


class StubAgentActionUseCase:
    def __init__(self):
        self.last_request = None

    async def execute_fire_and_forget(self, request):
        self.last_request = request
        return AgentActionOutput(
            run_id=request.wf_info.run_id if request.wf_info else "",
            node_id=request.wf_info.node_id if request.wf_info else "",
            task_id=request.wf_info.task_id if request.wf_info else None,
        )


def _make_app():
    main.build_app_container = lambda: {}
    app = create_app()
    app.dependency_overrides[require_bearer_token] = lambda: {
        "user_uuid": "user-1",
        "email": "user@example.com",
        "tenant_id": "tenant-1",
    }
    orchestrate_usecase = StubOrchestrateUseCase()
    dispatch_to_agent_usecase = StubDispatchToAgentUseCase()
    get_available_agents_usecase = StubGetAvailableAgentsUseCase()
    get_dispatch_result_usecase = StubGetDispatchResultUseCase()
    agent_action_usecase = StubAgentActionUseCase()
    app.state.container = {
        "_usecases": {
            "orchestrate.orchestrate": orchestrate_usecase,
            "orchestrate.dispatch_to_agent": dispatch_to_agent_usecase,
            "orchestrate.get_available_agents": get_available_agents_usecase,
            "orchestrate.get_dispatch_result": get_dispatch_result_usecase,
            "orchestrate.agent_action": agent_action_usecase,
        },
    }
    return app, orchestrate_usecase, dispatch_to_agent_usecase, get_dispatch_result_usecase, agent_action_usecase


def _make_request(authorization: str = "") -> Request:
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode("utf-8")))
    return Request({"type": "http", "headers": headers})


@pytest.mark.asyncio
async def test_orchestrate_async_maps_request_into_usecase_input():
    app, orchestrate_usecase, _, _, _ = _make_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/orchestrate/async",
            json={
                "message": "hello",
                "conv_id": "conv-123",
                "parameters": {"run_id": "run-123"},
            },
            headers={"authorization": "Bearer token-abc"},
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload == {
        "message": "hello",
        "conv_id": "conv-123",
        "uploaded_file_ids": [],
    }
    request = orchestrate_usecase.last_request
    assert request.message == "hello"
    assert request.conv_id == "conv-123"
    assert request.parameters == {"run_id": "run-123"}
    assert request.context == {"auth": {"authorization": "Bearer token-abc"}}


def test_build_orchestrate_input_maps_token_payload_and_headers():
    request = _build_orchestrate_input(
        OrchestrationRequestBody(
            message="hello",
            conv_id="conv-456",
            parameters={"run_id": "run-123"},
        ),
        {"user_uuid": "u1", "email": "u1@example.com", "tenant_id": "t1"},
        _make_request("Bearer token-abc"),
    )

    assert request.message == "hello"
    assert request.conv_id == "conv-456"
    assert request.user_id == "u1"
    assert request.user_email == "u1@example.com"
    assert request.tenant_id == "t1"
    assert request.parameters == {"run_id": "run-123"}
    assert request.context == {"auth": {"authorization": "Bearer token-abc"}}


@pytest.mark.asyncio
async def test_dispatch_to_agent_maps_request_into_usecase_input():
    app, _, dispatch_to_agent_usecase, _, _ = _make_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/orchestrate/dispatch-to-agent",
            json={
                "message": "send to agent",
                "conv_id": "conv-789",
                "parameters": {
                    "agent_type": "workflow",
                    "agent_name": "Business Agent",
                    "file_id": "file-9",
                },
            },
            headers={"authorization": "Bearer token-xyz"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "status": "queued",
        "conv_id": "conv-789",
        "correlation_id": "corr-1",
        "request_id": "req-2",
        "error": None,
    }
    request = dispatch_to_agent_usecase.last_request
    assert request.message == "send to agent"
    assert request.conv_id == "conv-789"
    assert request.parameters == {
        "agent_type": "workflow",
        "agent_name": "Business Agent",
        "file_id": "file-9",
    }
    assert request.context == {"auth": {"authorization": "Bearer token-xyz"}}


def test_build_dispatch_to_agent_input_uses_sub_when_user_uuid_missing():
    request = _build_dispatch_to_agent_input(
        DispatchToAgentRequestBody(
            message="send to agent",
            conv_id="conv-789",
            parameters={"agent_name": "Business Agent"},
        ),
        {"sub": "u-sub", "email": "u1@example.com"},
        _make_request("Bearer token-xyz"),
    )

    assert request.message == "send to agent"
    assert request.conv_id == "conv-789"
    assert request.user_id == "u-sub"
    assert request.user_email == "u1@example.com"
    assert request.tenant_id == "default"
    assert request.parameters == {"agent_name": "Business Agent"}
    assert request.context == {"auth": {"authorization": "Bearer token-xyz"}}


@pytest.mark.asyncio
async def test_available_agents_route_returns_dropdown_payload():
    app, _, _, _, _ = _make_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/orchestrate/available-agents")

    assert response.status_code == 200
    assert response.json() == {
        "agents": [
            {
                "id": "agent-1",
                "name": "Business Agent",
                "kind": "workflow",
                "description": "Published business agent",
                "status": "active",
                "is_published": True,
            }
        ]
    }


@pytest.mark.asyncio
async def test_dispatch_result_route_returns_shared_state_payload():
    app, _, _, get_dispatch_result_usecase, _ = _make_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/orchestrate/dispatch-results/conv-321", params={"conv_id": "conv-321"})

    assert response.status_code == 200
    assert response.json() == {
        "conv_id": "conv-321",
        "current_task": {"task_id": "task-1", "status": "pending"},
        "shared_state": {
            "metadata": {"conversation_id": "conv-321"},
            "executions": [{"task_id": "task-1", "status": "pending"}],
        },
        "error": None,
    }
    assert get_dispatch_result_usecase.last_request.conv_id == "conv-321"


@pytest.mark.asyncio
async def test_dispatch_to_agent_ui_route_returns_html_page():
    app, _, _, _, _ = _make_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/orchestrate/dispatch-to-agent/ui")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Manual Agent Picker" in response.text


@pytest.mark.asyncio
async def test_agent_action_route_maps_request_into_usecase_input():
    app, _, _, _, agent_action_usecase = _make_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/orchestrate/agent-action",
            json={
                "wf_info": {
                    "run_id": "run-42",
                    "node_id": "node-9",
                    "task_id": "task-7",
                },
                "action": "button",
                "values": {"field": "value"},
                "button_label": "Approve",
                "form_data": {"section": "details"},
                "metadata": {
                    "source": "workspace",
                    "action_context": {
                        "source_event_id": "evt-42",
                        "source_conv_id": "conv-42",
                    },
                },
            },
            headers={"authorization": "Bearer token-sub"},
        )

    assert response.status_code == 202
    assert response.json() == {
        "run_id": "run-42",
        "node_id": "node-9",
        "task_id": "task-7",
    }
    request = agent_action_usecase.last_request
    assert request.action == "button"
    assert request.values == {"field": "value"}
    assert request.button_label == "Approve"
    assert request.form_data == {"section": "details"}
    assert request.metadata == {
        "source": "workspace",
        "action_context": {
            "source_event_id": "evt-42",
            "source_conv_id": "conv-42",
        },
    }
    assert request.wf_info.run_id == "run-42"
    assert request.wf_info.node_id == "node-9"
    assert request.wf_info.task_id == "task-7"


def test_build_agent_action_input_maps_nested_wf_info():
    request = _build_agent_action_input(
        AgentActionRequestBody(
            wf_info=SubConversationWfInfoBody(run_id="run-1", node_id="node-2", task_id="task-3"),
            action="workspace_submit",
            values={"field": "value"},
            button_label="Submit",
            form_data={"step": 1},
            metadata={"source": "ui"},
        )
    )

    assert request.wf_info is not None
    assert request.wf_info.run_id == "run-1"
    assert request.wf_info.node_id == "node-2"
    assert request.wf_info.task_id == "task-3"
    assert request.action == "workspace_submit"
    assert request.values == {"field": "value"}
    assert request.button_label == "Submit"
    assert request.form_data == {"step": 1}
    assert request.metadata == {"source": "ui"}
