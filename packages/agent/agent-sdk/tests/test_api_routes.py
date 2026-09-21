import inspect

import pytest
from fastapi.testclient import TestClient

from agent_sdk.layer1_domain.entities.agent_info import AgentInfo
from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
    ExecuteAgentInput,
    ExecuteAgentOutput,
)
from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app


class _StubExecuteUseCase:
    async def execute(self, request: ExecuteAgentInput) -> ExecuteAgentOutput:
        return ExecuteAgentOutput(
            message=f"[stub] {request.message}",
            status="success",
            correlation_id=request.correlation_id,
        )


class _StubGetInfoUseCase:
    def execute(self) -> AgentInfo:
        return AgentInfo(
            agent_type="test-agent", version="0.0.1", sdk_version="1.0.0", domain="test"
        )


@pytest.fixture
def client():
    container = {
        "execute_agent": _StubExecuteUseCase(),
        "get_agent_info": _StubGetInfoUseCase(),
    }
    app = create_agent_app(container)
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_execute_endpoint(client):
    payload = {
        "message": "Hello agent",
        "conv_id": "conv-1",
        "user_id": "user-1",
    }
    response = client.post("/api/v1/execute", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "[stub] Hello agent"
    assert data["status"] == "success"


def test_execute_route_preserves_error_metadata_fields():
    class _ErrorMetadataExecuteUseCase:
        async def execute(self, request: ExecuteAgentInput) -> ExecuteAgentOutput:
            return ExecuteAgentOutput(
                message="boom",
                status="error",
                error="boom",
                error_code="STEP_FAILED",
                related_step_id="step-42",
                is_critical=True,
                error_context={
                    "exception_type": "AgentSDKError",
                    "message": "boom",
                    "error_code": "STEP_FAILED",
                    "related_step_id": "step-42",
                    "is_critical": True,
                },
            )

    app = create_agent_app(
        {
            "execute_agent": _ErrorMetadataExecuteUseCase(),
            "get_agent_info": _StubGetInfoUseCase(),
        }
    )
    client = TestClient(app)

    response = client.post("/api/v1/execute", json={"message": "Hello agent"})

    assert response.status_code == 200
    data = response.json()
    assert data["related_step_id"] == "step-42"
    assert data["is_critical"] is True
    assert data["error_context"]["exception_type"] == "AgentSDKError"


def test_info_endpoint(client):
    response = client.get("/api/v1/info")
    assert response.status_code == 200
    data = response.json()
    assert data["agent_type"] == "test-agent"
    assert data["version"] == "0.0.1"
    assert data["sdk_version"] == "1.0.0"
    assert data["domain"] == "test"


def test_execute_agent_input_parameters_uses_field_default_factory():
    from pydantic.fields import FieldInfo

    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        ExecuteAgentInputPydantic,
    )

    field_info: FieldInfo = ExecuteAgentInputPydantic.model_fields["parameters"]
    assert (
        field_info.default_factory is not None
    ), "parameters must use Field(default_factory=dict), not a bare mutable default"
    assert field_info.default_factory is dict, "parameters default_factory must be dict"


def test_execute_route_handler_is_async():
    """The execute route handler must be an async def (coroutine function)."""
    from fastapi.routing import APIRoute

    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        create_router,
    )

    class _AsyncStubExecuteUseCase:
        async def execute(self, request: ExecuteAgentInput) -> ExecuteAgentOutput:
            return ExecuteAgentOutput(message="async", status="success")

    container = {"execute_agent": _AsyncStubExecuteUseCase()}
    router = create_router(container)
    execute_route: APIRoute | None = None
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == "/execute":
            execute_route = route
            break
    assert execute_route is not None, "Could not find /execute route"
    assert inspect.iscoroutinefunction(
        execute_route.endpoint
    ), "execute route handler must be async def (coroutine function)"


@pytest.mark.asyncio
async def test_execute_route_uses_async_use_case():
    """The execute route must await exec_uc.execute() (async use case works end-to-end)."""

    class _AsyncStubExecuteUseCase:
        def __init__(self):
            self.was_called = False

        async def execute(self, request: ExecuteAgentInput) -> ExecuteAgentOutput:
            self.was_called = True
            return ExecuteAgentOutput(
                message=f"[async stub] {request.message}",
                status="success",
                correlation_id=request.correlation_id,
            )

    async_stub = _AsyncStubExecuteUseCase()
    container = {"execute_agent": async_stub, "get_agent_info": _StubGetInfoUseCase()}
    app = create_agent_app(container)
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/api/v1/execute",
            json={"message": "hello async", "conv_id": "c1", "user_id": "u1"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "[async stub] hello async"
    assert async_stub.was_called is True


# --- Orchestrator payload compatibility tests ---


def test_execute_input_pydantic_accepts_reply_to():
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        ExecuteAgentInputPydantic,
    )

    obj = ExecuteAgentInputPydantic(message="hi", reply_to="orchestrator.x.response")
    assert obj.reply_to == "orchestrator.x.response"


def test_execute_input_pydantic_reply_to_defaults_to_none():
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        ExecuteAgentInputPydantic,
    )

    obj = ExecuteAgentInputPydantic(message="hi")
    assert obj.reply_to is None


def test_execute_input_pydantic_accepts_reply_topic():
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        ExecuteAgentInputPydantic,
    )

    obj = ExecuteAgentInputPydantic(message="hi", reply_topic="agent.reply.123")
    assert obj.reply_topic == "agent.reply.123"


def test_execute_input_pydantic_accepts_action():
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        ExecuteAgentInputPydantic,
    )

    obj = ExecuteAgentInputPydantic(message="hi", action="run_workflow")
    assert obj.action == "run_workflow"


def test_execute_input_pydantic_accepts_agent_type():
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        ExecuteAgentInputPydantic,
    )

    obj = ExecuteAgentInputPydantic(message="hi", agent_type="generic")
    assert obj.agent_type == "generic"


def test_execute_input_pydantic_accepts_intent():
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        ExecuteAgentInputPydantic,
    )

    obj = ExecuteAgentInputPydantic(message="hi", intent={"goal": "search"})
    assert obj.intent == {"goal": "search"}


def test_execute_input_pydantic_accepts_execution_context():
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        ExecuteAgentInputPydantic,
    )

    obj = ExecuteAgentInputPydantic(
        message="hi", execution_context={"trace_id": "t1", "user_id": "u1"}
    )
    assert obj.execution_context == {"trace_id": "t1", "user_id": "u1"}


def test_execute_input_pydantic_accepts_context_snapshot():
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        ExecuteAgentInputPydantic,
    )

    obj = ExecuteAgentInputPydantic(message="hi", context_snapshot={"workspace": "w1"})
    assert obj.context_snapshot == {"workspace": "w1"}


def test_execute_input_pydantic_legacy_fields_still_work(client):
    """Legacy callers sending only message/conv_id/user_id/tenant_id/parameters still succeed."""
    payload = {
        "message": "legacy call",
        "conv_id": "c-99",
        "user_id": "u-99",
        "tenant_id": "tenant-a",
        "parameters": {"key": "value"},
    }
    response = client.post("/api/v1/execute", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"


def test_agent_request_dataclass_accepts_new_fields():
    from agent_sdk.layer1_domain.entities.agent_request import AgentRequest

    req = AgentRequest(
        message="test",
        reply_to="orchestrator.x.response",
        reply_topic="agent.reply.123",
        action="run_workflow",
        agent_type="generic",
        intent={"goal": "search"},
        execution_context={"trace_id": "t1"},
        context_snapshot={"workspace": "w1"},
    )
    assert req.reply_to == "orchestrator.x.response"
    assert req.reply_topic == "agent.reply.123"
    assert req.action == "run_workflow"
    assert req.agent_type == "generic"
    assert req.intent == {"goal": "search"}
    assert req.execution_context == {"trace_id": "t1"}
    assert req.context_snapshot == {"workspace": "w1"}


def test_agent_request_dataclass_legacy_fields_work():
    from agent_sdk.layer1_domain.entities.agent_request import AgentRequest

    req = AgentRequest(
        message="hello",
        conv_id="c1",
        user_id="u1",
        tenant_id="t1",
        parameters={"k": "v"},
    )
    assert req.message == "hello"
    assert req.conv_id == "c1"
    assert req.user_id == "u1"


def test_execute_agent_input_accepts_new_fields():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
    )

    inp = ExecuteAgentInput(
        message="test",
        reply_to="orchestrator.x.response",
        reply_topic="agent.reply.123",
        action="run_workflow",
        agent_type="generic",
        intent={"goal": "search"},
        execution_context={"trace_id": "t1"},
        context_snapshot={"workspace": "w1"},
    )
    assert inp.reply_to == "orchestrator.x.response"
    assert inp.reply_topic == "agent.reply.123"
    assert inp.action == "run_workflow"
    assert inp.agent_type == "generic"
    assert inp.intent == {"goal": "search"}
    assert inp.execution_context == {"trace_id": "t1"}
    assert inp.context_snapshot == {"workspace": "w1"}


# ─────────────────────────────────────────────────────────────────────────────
# Layer boundary enforcement: Layer 3 agent_server must NOT import Layer 4 directly
# ─────────────────────────────────────────────────────────────────────────────


def test_create_agent_app_does_not_import_layer4_settings():
    """create_agent_app (Layer 3) must not import Layer 4 settings directly."""
    import importlib
    import sys

    mod_name = "agent_sdk.layer3_adapters.presenters.agent_server"
    mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
    source = inspect.getsource(mod)
    assert (
        "from agent_sdk.layer4_frameworks.config" not in source
    ), "agent_server/__init__.py must not contain 'from agent_sdk.layer4_frameworks.config' imports"
