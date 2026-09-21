from datetime import datetime, timezone

import httpx
import pytest

from app.layer4_frameworks.clients import agent_hub_client as agent_hub_module
from app.layer4_frameworks.clients import workflow_client as workflow_client_module
from app.layer4_frameworks.clients.agent_hub_client import AgentHubClient
from app.layer4_frameworks.clients.workflow_client import WorkflowClient


class _Logger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict]] = []

    def info(self, message: str, **kwargs) -> None:
        self.messages.append((message, kwargs))

    def warning(self, message: str, **kwargs) -> None:
        self.messages.append((message, kwargs))

    def error(self, message: str, **kwargs) -> None:
        self.messages.append((message, kwargs))

    def debug(self, message: str, **kwargs) -> None:
        self.messages.append((message, kwargs))


class _FakeResponse:
    def __init__(self, payload, *, exc: Exception | None = None) -> None:
        self._payload = payload
        self._exc = exc

    def raise_for_status(self) -> None:
        if self._exc is not None:
            raise self._exc

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[tuple[str, str, object | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str):
        self.calls.append(("GET", url, None))
        return self._response

    async def post(self, url: str, json=None):
        self.calls.append(("POST", url, json))
        return self._response


def _agent_payload(agent_id: str = "agent-1") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": agent_id,
        "name": "Agent One",
        "kind": "SERVICE",
        "status": "READY",
        "is_published": True,
        "description": "Agent description",
        "healthcheck_endpoint": "/health",
        "invoke_endpoint": "/invoke",
        "is_alive": True,
        "last_health_check_at": now,
        "version": "1.0.0",
        "config_type": "http",
        "capabilities": ["planning"],
        "agents": ["agent-2"],
        "workflows": ["workflow-1"],
        "tools": ["tool-1"],
        "metadata": {"owner": "qa"},
        "business": "simplemdg",
        "created_at": now,
        "updated_at": now,
    }


def _node_info_payload() -> dict:
    return {
        "run_id": "run-1",
        "node_id": "node-1",
        "node_name": "Collect inputs",
        "node_type": "form",
        "status": "WAITING",
        "input_schema": [],
        "edit_mode": "full",
        "data_mode": "scalar",
        "scalars": {"field": "value"},
        "arrays": [],
        "main_file_id": None,
        "main_links": {},
        "error": None,
        "error_code": "",
        "error_details": {},
    }


def _patch_async_client(monkeypatch, module, client: _FakeAsyncClient) -> None:
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda timeout: client)


@pytest.mark.asyncio
async def test_agent_hub_get_returns_none_when_base_url_missing():
    logger = _Logger()
    client = AgentHubClient("", logger)

    result = await client.get("agent-1")

    assert result is None
    assert "not configured" in logger.messages[0][0]


@pytest.mark.asyncio
async def test_agent_hub_get_returns_agent_model(monkeypatch):
    logger = _Logger()
    http_client = _FakeAsyncClient(_FakeResponse(_agent_payload()))
    _patch_async_client(monkeypatch, agent_hub_module, http_client)
    client = AgentHubClient("https://hub.example/", logger)

    result = await client.get("agent-1")

    assert result is not None
    assert result.id == "agent-1"
    assert http_client.calls == [
        ("GET", "https://hub.example/api/v1/agents/agent-1", None)
    ]


@pytest.mark.asyncio
async def test_agent_hub_get_returns_none_when_http_fails(monkeypatch):
    logger = _Logger()
    http_client = _FakeAsyncClient(
        _FakeResponse(None, exc=httpx.HTTPError("boom"))
    )
    _patch_async_client(monkeypatch, agent_hub_module, http_client)
    client = AgentHubClient("https://hub.example", logger)

    result = await client.get("agent-1")

    assert result is None
    assert "Failed to fetch agent info" in logger.messages[-1][0]


@pytest.mark.asyncio
async def test_agent_hub_get_tools_by_ids_returns_empty_when_base_url_missing():
    logger = _Logger()
    client = AgentHubClient("", logger)

    result = await client.get_tools_by_ids(["tool-1"])

    assert result == []
    assert "not configured" in logger.messages[0][0]


# @pytest.mark.asyncio
# async def test_agent_hub_get_tools_by_ids_returns_tools(monkeypatch):
#     logger = _Logger()
#     http_client = _FakeAsyncClient(
#         _FakeResponse(
#             {
#                 "tools": [
#                     {"id": "tool-1", "name": "Search", "description": "Search tool"}
#                 ],
#                 "total": 1,
#             }
#         )
#     )
#     _patch_async_client(monkeypatch, agent_hub_module, http_client)
#     client = AgentHubClient("https://hub.example/", logger)

#     result = await client.get_tools_by_ids(["tool-1"])

#     assert [tool.id for tool in result] == ["tool-1"]
#     assert http_client.calls == [
#         ("POST", "https://hub.example/api/v1/tools/by_ids", ["tool-1"])
#     ]


@pytest.mark.asyncio
async def test_agent_hub_get_tools_by_ids_returns_empty_when_http_fails(monkeypatch):
    logger = _Logger()
    http_client = _FakeAsyncClient(
        _FakeResponse(None, exc=httpx.HTTPError("boom"))
    )
    _patch_async_client(monkeypatch, agent_hub_module, http_client)
    client = AgentHubClient("https://hub.example", logger)

    result = await client.get_tools_by_ids(["tool-1"])

    assert result == []
    assert logger.messages[-1][1]["ids"] == ["tool-1"]


@pytest.mark.asyncio
async def test_agent_hub_get_workflows_by_ids_returns_empty_when_base_url_missing():
    logger = _Logger()
    client = AgentHubClient("", logger)

    result = await client.get_workflows_by_ids(["workflow-1"])

    assert result == []
    assert "not configured" in logger.messages[0][0]


# @pytest.mark.asyncio
# async def test_agent_hub_get_workflows_by_ids_returns_workflows(monkeypatch):
#     logger = _Logger()
#     http_client = _FakeAsyncClient(
#         _FakeResponse(
#             {
#                 "workflows": [
#                     {
#                         "id": "workflow-1",
#                         "name": "CreateTemplate",
#                         "description": "Creates a template",
#                     }
#                 ],
#                 "total": 1,
#             }
#         )
#     )
#     _patch_async_client(monkeypatch, agent_hub_module, http_client)
#     client = AgentHubClient("https://hub.example/", logger)

#     result = await client.get_workflows_by_ids(["workflow-1"])

#     assert [workflow.id for workflow in result] == ["workflow-1"]
#     assert http_client.calls == [
#         (
#             "POST",
#             "https://hub.example/api/v1/workflows/by_ids",
#             ["workflow-1"],
#         )
#     ]


@pytest.mark.asyncio
async def test_agent_hub_get_workflows_by_ids_returns_empty_when_http_fails(
    monkeypatch,
):
    logger = _Logger()
    http_client = _FakeAsyncClient(
        _FakeResponse(None, exc=httpx.HTTPError("boom"))
    )
    _patch_async_client(monkeypatch, agent_hub_module, http_client)
    client = AgentHubClient("https://hub.example", logger)

    result = await client.get_workflows_by_ids(["workflow-1"])

    assert result == []
    assert logger.messages[-1][1]["ids"] == ["workflow-1"]


@pytest.mark.asyncio
async def test_agent_hub_get_agents_by_ids_returns_empty_when_base_url_missing():
    logger = _Logger()
    client = AgentHubClient("", logger)

    result = await client.get_agents_by_ids(["agent-1"])

    assert result == []
    assert "not configured" in logger.messages[0][0]


@pytest.mark.asyncio
async def test_agent_hub_get_agents_by_ids_returns_agents(monkeypatch):
    logger = _Logger()
    http_client = _FakeAsyncClient(_FakeResponse([_agent_payload("agent-2")]))
    _patch_async_client(monkeypatch, agent_hub_module, http_client)
    client = AgentHubClient("https://hub.example/", logger)

    result = await client.get_agents_by_ids(["agent-2"])

    assert [agent.id for agent in result] == ["agent-2"]
    assert http_client.calls == [
        ("POST", "https://hub.example/api/v1/agents/by_ids", ["agent-2"])
    ]


@pytest.mark.asyncio
async def test_agent_hub_get_agents_by_ids_returns_empty_when_http_fails(monkeypatch):
    logger = _Logger()
    http_client = _FakeAsyncClient(
        _FakeResponse(None, exc=httpx.HTTPError("boom"))
    )
    _patch_async_client(monkeypatch, agent_hub_module, http_client)
    client = AgentHubClient("https://hub.example", logger)

    result = await client.get_agents_by_ids(["agent-2"])

    assert result == []
    assert logger.messages[-1][1]["ids"] == ["agent-2"]


@pytest.mark.asyncio
async def test_workflow_client_get_by_id_returns_static_workflow():
    logger = _Logger()
    client = WorkflowClient("https://workflow.example", logger)

    result = await client.get_by_id("workflow-1")

    assert result is not None
    assert result.id == "workflow_001"
    assert result.name == "trigger workflow create_template"


@pytest.mark.asyncio
async def test_workflow_client_get_node_info_returns_none_when_base_url_missing():
    logger = _Logger()
    client = WorkflowClient("", logger)

    result = await client.get_node_info("run-1", "node-1")

    assert result is None
    assert "not configured" in logger.messages[0][0]


@pytest.mark.asyncio
async def test_workflow_client_get_node_info_returns_model(monkeypatch):
    logger = _Logger()
    # SA-890: get_node_info now fetches the live /runs/{run}/nodes/{node}/schema
    # endpoint and reshapes its data.schema into WorkNodeStatus.input_schema.
    schema_payload = {
        "status": "ok",
        "data": {
            "schema": [
                {
                    "key": "triggerId",
                    "outputType": "string",
                    "fieldConfig": {
                        "fieldWrapper": "FormItemWrapper",
                        "fieldControl": "InputControl",
                        "wrapperProps": {"label": "Data Input ID", "description": "", "required": True},
                        "controlProps": {"placeholder": "Input ID"},
                        "rules": [{"method": "required"}],
                    },
                }
            ]
        },
    }
    http_client = _FakeAsyncClient(_FakeResponse(schema_payload))
    _patch_async_client(monkeypatch, workflow_client_module, http_client)
    client = WorkflowClient("https://workflow.example/", logger)

    result = await client.get_node_info("run-1", "node-1")

    assert result is not None
    assert result.node_id == "node-1"
    assert [f.key for f in result.input_schema] == ["triggerId"]
    assert http_client.calls == [
        (
            "GET",
            "https://workflow.example/api/v1/runs/run-1/nodes/node-1/schema",
            None,
        )
    ]


def test_worknodestatus_from_schema_response_parses_nested_and_optional_fields():
    # SA-890: the live /schema payload mixes form-item fields (fieldControl/controlProps/
    # rules) with table wrappers that omit them, table wrapperProps that carry
    # isAdd/isDelete/size, textarea controlProps with rowsLength, and rows with nested
    # `fields`. WorkNodeStatus must parse all of it without a `**`-splat / KeyError crash.
    from dataclasses import asdict

    from app.layer1_domain.entities.workflow_entity import WorkNodeStatus

    payload = {
        "status": "ok",
        "data": {
            "schema": [
                {
                    "key": "template_name",
                    "outputType": "string",
                    "fieldConfig": {
                        "fieldWrapper": "FormItemWrapper",
                        "wrapperProps": {"label": "Template name", "description": "..."},
                        "fieldControl": "InputControl",
                        "controlProps": {"placeholder": "e.g. Customer master list"},
                        "rules": [{"method": "required", "message": "Please name this template"}],
                    },
                },
                {
                    "key": "description",
                    "outputType": "string",
                    "fieldConfig": {
                        "fieldWrapper": "FormItemWrapper",
                        "wrapperProps": {"label": "Description", "description": "..."},
                        "fieldControl": "TextareaControl",
                        "controlProps": {"rowsLength": 2, "placeholder": "..."},
                    },
                },
                {
                    "key": "rules",
                    "outputType": "array",
                    "fieldConfig": {
                        "fieldWrapper": "TableWrapper",
                        "wrapperProps": {"label": "Duplicate rules", "isAdd": True, "isDelete": True},
                    },
                    "fields": [
                        {
                            "key": "0",
                            "outputType": "object",
                            "fieldConfig": {
                                "fieldWrapper": "TableRowWrapper",
                                "wrapperProps": {"isAdd": True, "isDelete": True},
                            },
                            "fields": [
                                {
                                    "key": "rule",
                                    "outputType": "string",
                                    "fieldConfig": {
                                        "fieldWrapper": "TableCellWrapper",
                                        "wrapperProps": {"label": "Rule", "size": 460},
                                        "fieldControl": "InputControl",
                                        "controlProps": {"placeholder": "..."},
                                    },
                                }
                            ],
                        }
                    ],
                },
            ],
            "node_kind": "INPUT",
            "node_state": "PENDING",
            "node": {"name": "Deduplication template", "node_type": "INPUT"},
            "error": None,
            "error_code": "",
            "error_details": {},
        },
    }

    result = WorkNodeStatus.from_schema_response(payload, run_id="r1", node_id="n1")

    # envelope mapped from data.node / node_state
    assert result.node_name == "Deduplication template"
    assert result.node_type == "INPUT"
    assert result.status == "PENDING"

    # top-level keys + label -> key mapping the planner relies on
    assert [f.key for f in result.input_schema] == ["template_name", "description", "rules"]
    assert result.input_schema[0].fieldConfig.wrapperProps.label == "Template name"
    assert result.input_schema[0].fieldConfig.rules[0].message == "Please name this template"
    assert result.input_schema[1].fieldConfig.controlProps.rowsLength == 2

    # table wrapper: optional control fields absent -> None, wrapper flags parsed
    table = result.input_schema[2]
    assert table.fieldConfig.fieldControl is None
    assert table.fieldConfig.controlProps is None
    assert table.fieldConfig.rules is None
    assert table.fieldConfig.wrapperProps.isAdd is True

    # nested rows/cells parsed recursively
    cell = table.fields[0].fields[0]
    assert cell.key == "rule"
    assert cell.fieldConfig.wrapperProps.size == 460

    # planner serializes node_info via asdict — must not raise
    asdict(result)


@pytest.mark.asyncio
async def test_workflow_client_get_node_info_returns_none_when_http_fails(monkeypatch):
    logger = _Logger()
    http_client = _FakeAsyncClient(
        _FakeResponse(None, exc=httpx.HTTPError("boom"))
    )
    _patch_async_client(monkeypatch, workflow_client_module, http_client)
    client = WorkflowClient("https://workflow.example", logger)

    result = await client.get_node_info("run-1", "node-1")

    assert result is None
    assert "Failed to fetch node info" in logger.messages[-1][0]