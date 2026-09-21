import pytest
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.layer2_application.features.execute_agent.use_cases.execute_graph_use_case import (
    ExecuteGraphUseCase,
)
from app.layer3_adapters.controllers.restful.v1.profile_manager_controller import (
    create_router,
)
from app.layer3_adapters.controllers.restful.v1.dtos.execute_dto import (
    ExecuteInputDto,
    ExecuteOutputDto,
)
from app.layer4_frameworks.middleware.auth import AuthMiddleware


@pytest.fixture
def mock_graph():
    graph = AsyncMock()
    graph.ainvoke.return_value = {
        "formatted_response": {
            "status": "success",
            "content": "done",
        }
    }
    return graph


@pytest.fixture
def mock_token_provider():
    provider = AsyncMock()
    provider.get_token.side_effect = Exception("No token available")
    return provider


@pytest.fixture
def client(mock_graph, mock_token_provider):
    use_case = ExecuteGraphUseCase(graph=mock_graph)
    app = FastAPI()
    app.add_middleware(AuthMiddleware, token_provider=mock_token_provider)
    app.include_router(create_router(use_case))
    return TestClient(app, raise_server_exceptions=False)


def _initial_state(mock_graph) -> dict:
    return mock_graph.ainvoke.call_args[0][0]


def _config(mock_graph) -> dict:
    return mock_graph.ainvoke.call_args.kwargs["config"]


# --- token extraction ---

def test_token_placed_in_config_not_state(client, mock_graph):
    client.post(
        "/execute",
        json={"message": "hello"},
        headers={"Authorization": "Bearer my-secret-token"},
    )
    assert _config(mock_graph)["configurable"]["token"] == "my-secret-token"
    assert "token" not in _initial_state(mock_graph)


def test_missing_token_returns_error(client):
    resp = client.post("/execute", json={"message": "hello"})
    assert resp.status_code == 500


# --- payload mapping ---

def test_all_payload_fields_mapped_to_state(client, mock_graph):
    client.post(
        "/execute",
        json={
            "message": "test msg",
            "conv_id": "conv-123",
            "user_id": "user-1",
            "tenant_id": "tenant-1",
            "source": "web",
            "parameters": {"k": "v"},
        },
        headers={"Authorization": "Bearer tok"},
    )
    state = _initial_state(mock_graph)
    assert state["message"] == "test msg"
    assert state["conv_id"] == "conv-123"
    assert state["user_id"] == "user-1"
    assert state["tenant_id"] == "tenant-1"
    assert state["source"] == "web"
    assert state["parameters"] == {"k": "v"}
    assert state["transport_state"] == "IDLE"
    assert "token" not in state


def test_default_fields_applied_when_omitted(client, mock_graph):
    client.post(
        "/execute",
        json={"message": "hello"},
        headers={"Authorization": "Bearer tok"},
    )
    state = _initial_state(mock_graph)
    assert state["user_id"] == "anonymous"
    assert state["tenant_id"] == "default"
    assert state["source"] == "api"
    assert state["parameters"] == {}


def test_conv_id_auto_generated_when_empty(client, mock_graph):
    client.post(
        "/execute",
        json={"message": "hello", "conv_id": ""},
        headers={"Authorization": "Bearer tok"},
    )
    assert _initial_state(mock_graph)["conv_id"] != ""


def test_conv_id_used_as_graph_thread_id(client, mock_graph):
    client.post(
        "/execute",
        json={"message": "hello", "conv_id": "my-thread"},
        headers={"Authorization": "Bearer tok"},
    )
    assert _config(mock_graph)["configurable"]["thread_id"] == "my-thread"


def test_generated_conv_id_matches_thread_id(client, mock_graph):
    client.post(
        "/execute",
        json={"message": "hello", "conv_id": ""},
        headers={"Authorization": "Bearer tok"},
    )
    state = _initial_state(mock_graph)
    config = _config(mock_graph)
    assert state["conv_id"] == config["configurable"]["thread_id"]


# --- response ---

def test_returns_formatted_response_from_graph(client, mock_graph):
    mock_graph.ainvoke.return_value = {
        "formatted_response": {"status": "success", "content": "ok"}
    }
    resp = client.post(
        "/execute",
        json={"message": "hello"},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "content": "ok"}


def test_returns_empty_dict_when_no_formatted_response(client, mock_graph):
    mock_graph.ainvoke.return_value = {}
    resp = client.post(
        "/execute",
        json={"message": "hello"},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 200
    assert resp.json() == {}


# --- validation ---

def test_missing_message_returns_422(client):
    resp = client.post(
        "/execute",
        json={},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 422


# --- DTO fidelity (P7): ExecuteOutputDto mirrors formatted_response exactly ---

def test_execute_output_dto_round_trips_success_shape():
    formatted_response = {
        "content": "Profile Manager Agent execute successfully.",
        "status": "success",
        "agent_type": "profile-manager",
        "agent_data": {
            "agent_type": "profile-manager",
            "user_prompt": "find alice",
            "expression": "%24filter=userName%20eq%20%27alice%27",
            "valid": "correct",
            "message": "",
            "item_size": "1",
        },
        "transport_state": "RECEIVED",
    }
    dto = ExecuteOutputDto(**formatted_response)
    assert dto.model_dump(exclude_none=True) == formatted_response


def test_execute_output_dto_omits_transport_state_for_error_shape():
    # error/not-support responses carry no transport_state — exclude_none drops it
    formatted_response = {
        "content": "Profile Manager Agent not supported.",
        "status": "error",
        "agent_type": "profile-manager",
        "agent_data": {
            "agent_type": "profile-manager",
            "valid": "incorrect",
            "message": "Profile Manager Agent not supported.",
        },
    }
    dto = ExecuteOutputDto(**formatted_response)
    assert dto.model_dump(exclude_none=True) == formatted_response


def test_execute_input_dto_defaults_match_old_execute_request():
    dto = ExecuteInputDto(message="hi")
    assert dto.user_id == "anonymous"
    assert dto.tenant_id == "default"
    assert dto.source == "api"
    assert dto.parameters == {}
    assert dto.conv_id == ""
