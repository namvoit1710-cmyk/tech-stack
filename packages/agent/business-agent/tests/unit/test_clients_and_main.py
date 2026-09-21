import asyncio

import httpx
import pytest

from app.layer4_frameworks.clients import agent_hub_client as agent_hub_client_module
from app.layer4_frameworks.clients import workflow_client as workflow_client_module
from app.layer4_frameworks.clients.agent_hub_client import AgentHubClient
from app.layer4_frameworks.clients.workflow_client import WorkflowClient


class RecordingLogger:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str, dict]] = []

    def info(self, message: str, **kwargs) -> None:
        self.entries.append(("info", message, kwargs))

    def error(self, message: str, **kwargs) -> None:
        self.entries.append(("error", message, kwargs))

    def warning(self, message: str, **kwargs) -> None:
        self.entries.append(("warning", message, kwargs))

    def debug(self, message: str, **kwargs) -> None:
        self.entries.append(("debug", message, kwargs))


class DummyResponse:
    def __init__(self, payload, exc: Exception | None = None) -> None:
        self._payload = payload
        self._exc = exc

    def raise_for_status(self) -> None:
        if self._exc is not None:
            raise self._exc

    def json(self):
        return self._payload


class DummyAsyncClient:
    def __init__(self, response: DummyResponse) -> None:
        self._response = response
        self.calls: list[tuple[str, str, object | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str):
        self.calls.append(("GET", url, None))
        return self._response

    async def post(self, url: str, json=None):
        self.calls.append(("POST", url, json))
        return self._response


def _agent_payload(agent_id: str = "agent-1") -> dict:
    return {
        "id": agent_id,
        "name": "Agent",
        "kind": "SERVICE",
        "status": "READY",
        "is_published": True,
        "description": "Agent description",
        "healthcheck_endpoint": "/health",
        "invoke_endpoint": "/invoke",
        "is_alive": True,
        "last_health_check_at": None,
        "version": "1.0.0",
        "config_type": "json",
        "capabilities": ["plan"],
        "agents": [],
        "workflows": [],
        "tools": [],
        "metadata": {},
        "business": "business",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "args", "expected", "message_fragment"),
    [
        ("get", ("agent-1",), None, "Agent Hub API URL is not configured"),
        (
            "get_tools_by_ids",
            (["tool-1"],),
            [],
            "Agent Hub API URL is not configured. Cannot fetch tool info",
        ),
        (
            "get_workflows_by_ids",
            (["workflow-1"],),
            [],
            "Agent Hub API URL is not configured. Cannot fetch workflow info",
        ),
        (
            "get_agents_by_ids",
            (["agent-1"],),
            [],
            "Agent Hub API URL is not configured. Cannot fetch agent info",
        ),
    ],
)
async def test_agent_hub_client_returns_empty_result_when_base_url_missing(
    method_name, args, expected, message_fragment
):
    logger = RecordingLogger()
    client = AgentHubClient("", logger)

    result = await getattr(client, method_name)(*args)

    assert result == expected
    assert any(message_fragment in message for _, message, _ in logger.entries)


# @pytest.mark.asyncio
# @pytest.mark.parametrize(
#     (
#         "method_name",
#         "args",
#         "payload",
#         "verb",
#         "expected_url",
#         "expected_body",
#         "expected_first_id",
#     ),
#     [
#         (
#             "get",
#             ("agent-1",),
#             _agent_payload("agent-1"),
#             "GET",
#             "http://hub/api/v1/agents/agent-1",
#             None,
#             "agent-1",
#         ),
#         (
#             "get_tools_by_ids",
#             (["tool-1"],),
#             {"tools": [{"id": "tool-1", "name": "Tool", "description": "desc"}], "total": 1},
#             "POST",
#             "http://hub/api/v1/tools/by_ids",
#             ["tool-1"],
#             "tool-1",
#         ),
#         (
#             "get_workflows_by_ids",
#             (["workflow-1"],),
#             {"workflows": [{"id": "workflow-1", "name": "Workflow", "description": "desc"}], "total": 1},
#             "POST",
#             "http://hub/api/v1/workflows/by_ids",
#             ["workflow-1"],
#             "workflow-1",
#         ),
#         (
#             "get_agents_by_ids",
#             (["agent-1"],),
#             [_agent_payload("agent-1")],
#             "POST",
#             "http://hub/api/v1/agents/by_ids",
#             ["agent-1"],
#             "agent-1",
#         ),
#     ],
# )
# async def test_agent_hub_client_success_paths(
#     monkeypatch,
#     method_name,
#     args,
#     payload,
#     verb,
#     expected_url,
#     expected_body,
#     expected_first_id,
# ):
#     logger = RecordingLogger()
#     subject = AgentHubClient("http://hub/", logger)
#     http_client = DummyAsyncClient(DummyResponse(payload))
#     monkeypatch.setattr(
#         agent_hub_client_module.httpx,
#         "AsyncClient",
#         lambda timeout: http_client,
#     )

#     result = await getattr(subject, method_name)(*args)

#     assert http_client.calls == [(verb, expected_url, expected_body)]
#     if isinstance(result, list):
#         assert result[0].id == expected_first_id
#     else:
#         assert result.id == expected_first_id


# @pytest.mark.asyncio
# @pytest.mark.parametrize(
#     ("method_name", "args", "payload", "expected", "message_fragment"),
#     [
#         (
#             "get",
#             ("agent-1",),
#             _agent_payload("agent-1"),
#             None,
#             "Failed to fetch agent info for agent-1",
#         ),
#         (
#             "get_tools_by_ids",
#             (["tool-1"],),
#             {"tools": [], "total": 0},
#             [],
#             "Failed to fetch tool info",
#         ),
#         (
#             "get_workflows_by_ids",
#             (["workflow-1"],),
#             {"workflows": [], "total": 0},
#             [],
#             "Failed to fetch workflow info",
#         ),
#         (
#             "get_agents_by_ids",
#             (["agent-1"],),
#             [],
#             [],
#             "Failed to fetch agent info",
#         ),
#     ],
# )
# async def test_agent_hub_client_returns_empty_result_on_http_error(
#     monkeypatch, method_name, args, payload, expected, message_fragment
# ):
#     logger = RecordingLogger()
#     subject = AgentHubClient("http://hub", logger)
#     http_client = DummyAsyncClient(
#         DummyResponse(payload, exc=httpx.HTTPStatusError("boom", request=None, response=None))
#     )
#     monkeypatch.setattr(
#         agent_hub_client_module.httpx,
#         "AsyncClient",
#         lambda timeout: http_client,
#     )

#     result = await getattr(subject, method_name)(*args)

#     assert result == expected
#     assert any(message_fragment in message for _, message, _ in logger.entries)


# @pytest.mark.asyncio
# async def test_workflow_client_get_by_id_returns_stub_workflow():
#     subject = WorkflowClient("http://workflow", RecordingLogger())

#     workflow = await subject.get_by_id("workflow-1")

#     assert workflow.id == "workflow_001"
#     assert workflow.name == "trigger workflow create_template"


@pytest.mark.asyncio
async def test_workflow_client_get_node_info_returns_none_when_base_url_missing():
    logger = RecordingLogger()
    subject = WorkflowClient("", logger)

    result = await subject.get_node_info("run-1", "node-1")

    assert result is None
    assert any("Workflow API URL is not configured" in message for _, message, _ in logger.entries)


# @pytest.mark.asyncio
# async def test_workflow_client_get_node_info_success(monkeypatch):
#     logger = RecordingLogger()
#     subject = WorkflowClient("http://workflow/", logger)
#     payload = {
#         "run_id": "run-1",
#         "node_id": "node-1",
#         "node_name": "Node",
#         "node_type": "form",
#         "status": "waiting",
#         "input_schema": [],
#         "edit_mode": "none",
#         "data_mode": "scalar",
#         "scalars": {},
#         "arrays": [],
#         "main_file_id": None,
#         "main_links": {},
#         "error": None,
#         "error_code": "",
#         "error_details": {},
#     }
#     http_client = DummyAsyncClient(DummyResponse(payload))
#     monkeypatch.setattr(
#         workflow_client_module.httpx,
#         "AsyncClient",
#         lambda timeout: http_client,
#     )

#     result = await subject.get_node_info("run-1", "node-1")

#     assert result.node_id == "node-1"
#     assert http_client.calls == [
#         (
#             "GET",
#             "http://workflow/api/v1/workflows/run-1/nodes/node-1",
#             None,
#         )
#     ]


@pytest.mark.asyncio
async def test_workflow_client_get_node_info_returns_none_on_http_error(monkeypatch):
    logger = RecordingLogger()
    subject = WorkflowClient("http://workflow", logger)
    http_client = DummyAsyncClient(
        DummyResponse({}, exc=httpx.HTTPError("boom"))
    )
    monkeypatch.setattr(
        workflow_client_module.httpx,
        "AsyncClient",
        lambda timeout: http_client,
    )

    result = await subject.get_node_info("run-1", "node-1")

    assert result is None
    assert any("Failed to fetch node info for node-1" in message for _, message, _ in logger.entries)


def test_create_app_builds_container_and_delegates(main_module, monkeypatch):
    container = {"_dependencies": {}}
    sentinel_app = object()
    monkeypatch.setattr(main_module, "build_app_container", lambda: container)
    monkeypatch.setattr(main_module, "create_agent_app", lambda value: sentinel_app if value is container else None)

    assert main_module.create_app() is sentinel_app


def test_run_invokes_consumer_agent_for_consumer_mode(main_module, monkeypatch):
    calls = {}
    original_asyncio_run = asyncio.run
    monkeypatch.setattr(main_module.settings, "APP_MODE", "CONSUMER", raising=False)
    monkeypatch.setattr(main_module, "build_app_container", lambda: {"container": True})

    async def fake_run_consumer_agent(container):
        calls["container"] = container

    monkeypatch.setattr(main_module, "run_consumer_agent", fake_run_consumer_agent)
    monkeypatch.setattr(main_module.asyncio, "run", lambda coro: original_asyncio_run(coro))

    main_module.run()

    assert calls["container"] == {"container": True}


@pytest.mark.parametrize("mode", ["SERVER", "HYBRID"])
def test_run_invokes_uvicorn_for_server_modes(main_module, monkeypatch, mode):
    calls = {}
    monkeypatch.setattr(main_module.settings, "APP_MODE", mode, raising=False)
    monkeypatch.setattr(main_module.settings, "SERVER_PORT", 36001, raising=False)
    monkeypatch.setattr(main_module.settings, "SERVER_HOST", "127.0.0.1", raising=False)
    monkeypatch.setattr(main_module, "create_app", lambda: "app-instance")
    monkeypatch.setattr(
        main_module.uvicorn,
        "run",
        lambda app, host, port: calls.update({"app": app, "host": host, "port": port}),
    )

    main_module.run()

    assert calls == {"app": "app-instance", "host": "127.0.0.1", "port": 36001}


def test_run_rejects_unknown_app_mode(main_module, monkeypatch):
    monkeypatch.setattr(main_module.settings, "APP_MODE", "UNKNOWN", raising=False)

    with pytest.raises(ValueError, match="Unknown APP_MODE=UNKNOWN"):
        main_module.run()