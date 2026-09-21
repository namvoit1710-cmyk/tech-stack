from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


def _parameters_schema():
    return {
        "type": "object",
        "properties": {
            "a": {"type": "integer"},
            "b": {"type": "integer"},
        },
        "required": ["a", "b"],
    }


def _default_output_schema():
    return {"type": "object", "properties": {}}


def _make_response(payload, *, status_code=200):
    response = MagicMock(status_code=status_code)
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    return response


def _make_tool_registration():
    from agent_sdk.layer1_domain.entities.tool_registration import ToolRegistration

    return ToolRegistration(
        tool_name="add",
        registry_name="agent__planner__add__1.2.3",
        logical_registry_name="agent__planner__add",
        description="Add two integers.",
        version="1.2.3",
        parameters_schema=_parameters_schema(),
        response_schema=_default_output_schema(),
        metadata={
            "inline": True,
            "langchain_tool": True,
            "sdk_owned": True,
            "owner_kind": "agent",
            "owner_name": "planner",
            "owner_version": "1.2.3",
            "tool_name": "add",
            "logical_registry_name": "agent__planner__add",
        },
    )


def _make_full_tool(
    *,
    tool_id="tool-existing",
    name="agent__planner__add__1.2.3",
    version="1.2.3",
    status="active",
):
    return {
        "id": tool_id,
        "name": name,
        "description": "Add two integers.",
        "version": version,
        "status": status,
        "input_data": _parameters_schema(),
        "output_data": _default_output_schema(),
        "metadata": {
            "inline": True,
            "langchain_tool": True,
            "sdk_owned": True,
            "owner_kind": "agent",
            "owner_name": "planner",
            "owner_version": version,
            "tool_name": "add",
            "logical_registry_name": "agent__planner__add",
        },
    }


@pytest.mark.asyncio
async def test_sync_tools_posts_full_inline_tool_payload_for_missing_tool():
    from agent_sdk.layer4_frameworks.registry.http_tool_registry import HttpToolRegistry

    registry = HttpToolRegistry.__new__(HttpToolRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()

    list_response = _make_response({"tools": []})
    create_response = _make_response({"id": "tool-123"})

    mock_client.get = AsyncMock(return_value=list_response)
    mock_client.post = AsyncMock(return_value=create_response)
    registry._client = mock_client

    registration = _make_tool_registration()

    result = await registry.sync_tools([registration])

    assert result == {"add": "tool-123"}
    mock_client.get.assert_awaited_once()
    assert mock_client.get.call_args.args[0].endswith("/api/v1/tools/all")
    mock_client.post.assert_awaited_once()
    assert mock_client.post.call_args.args[0].endswith("/api/v1/tools/register")

    post_payload = mock_client.post.call_args.kwargs["json"]
    assert post_payload["name"] == "agent__planner__add__1.2.3"
    assert post_payload["description"] == "Add two integers."
    assert post_payload["protocol"] == "mcp"
    assert post_payload["endpoint"] == "inline"
    assert post_payload["version"] == "1.2.3"
    assert post_payload["status"] == "active"
    assert post_payload["input_data"] == _parameters_schema()
    assert post_payload["output_data"] == _default_output_schema()
    assert post_payload["auth_config"] == {}

    expected_hash = HttpToolRegistry._registration_definition_hash(registration)
    assert post_payload["metadata"] == {
        "inline": True,
        "langchain_tool": True,
        "sdk_owned": True,
        "owner_kind": "agent",
        "owner_name": "planner",
        "owner_version": "1.2.3",
        "tool_name": "add",
        "logical_registry_name": "agent__planner__add",
        "definition_hash": expected_hash,
        "definition_hash_algorithm": "sha256:v1",
    }


@pytest.mark.asyncio
async def test_sync_tools_reuses_existing_registry_tool_id_by_matching_definition_hash():
    from agent_sdk.layer4_frameworks.registry.http_tool_registry import HttpToolRegistry

    registry = HttpToolRegistry.__new__(HttpToolRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()

    list_response = _make_response(
        {
            "tools": [
                {
                    "id": "tool-existing",
                    "name": "agent__planner__add__1.2.3",
                }
            ]
        }
    )
    by_ids_response = _make_response({"tools": [_make_full_tool()]})

    mock_client.get = AsyncMock(return_value=list_response)
    mock_client.post = AsyncMock(return_value=by_ids_response)
    registry._client = mock_client

    result = await registry.sync_tools([_make_tool_registration()])

    assert result == {"add": "tool-existing"}
    mock_client.get.assert_awaited_once()
    mock_client.post.assert_awaited_once()
    assert mock_client.post.call_args.args[0].endswith("/api/v1/tools/by_ids")


@pytest.mark.asyncio
async def test_sync_tools_registers_bumped_version_when_existing_hash_differs():
    from agent_sdk.layer4_frameworks.registry.http_tool_registry import HttpToolRegistry

    registry = HttpToolRegistry.__new__(HttpToolRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()

    list_response = _make_response(
        {
            "tools": [
                {
                    "id": "tool-old",
                    "name": "agent__planner__add__0.10",
                }
            ]
        }
    )
    old_tool_response = _make_response(
        {
            "tools": [
                {
                    **_make_full_tool(
                        tool_id="tool-old",
                        name="agent__planner__add__0.10",
                        version="0.10",
                    ),
                    "description": "Old add implementation.",
                }
            ]
        }
    )
    create_response = _make_response({"id": "tool-new"})
    deactivate_response = _make_response({"id": "tool-old", "status": "inactive"})

    mock_client.get = AsyncMock(return_value=list_response)
    mock_client.post = AsyncMock(
        side_effect=[old_tool_response, create_response, deactivate_response]
    )
    registry._client = mock_client

    result = await registry.sync_tools([_make_tool_registration()])

    assert result == {"add": "tool-new"}
    assert mock_client.post.await_count == 3

    create_call = mock_client.post.await_args_list[1]
    assert create_call.args[0].endswith("/api/v1/tools/register")
    # _next_version(["0.10"], "1.2.3") → max is "1.2.3" (semver) → bumped patch → "1.2.4"
    assert create_call.kwargs["json"]["name"] == "agent__planner__add__1.2.4"
    assert create_call.kwargs["json"]["version"] == "1.2.4"
    assert (
        create_call.kwargs["json"]["metadata"]["registry_version_bump_reason"]
        == "definition_hash_changed"
    )

    deactivate_call = mock_client.post.await_args_list[2]
    assert deactivate_call.args[0].endswith("/api/v1/tools/tool-old/deactivate")


@pytest.mark.asyncio
async def test_sync_tools_refetches_all_when_register_returns_conflict():
    from agent_sdk.layer4_frameworks.registry.http_tool_registry import HttpToolRegistry

    registry = HttpToolRegistry.__new__(HttpToolRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()

    first_list_response = _make_response({"tools": []})
    second_list_response = _make_response(
        {
            "tools": [
                {
                    "id": "tool-existing",
                    "name": "agent__planner__add__1.2.3",
                }
            ]
        }
    )
    by_ids_response = _make_response({"tools": [_make_full_tool()]})
    activate_response = _make_response({"id": "tool-existing", "status": "active"})

    conflict_error = httpx.HTTPStatusError(
        "conflict",
        request=httpx.Request("POST", "http://registry/api/v1/tools/register"),
        response=httpx.Response(409),
    )

    create_response = MagicMock(status_code=409)
    create_response.raise_for_status = MagicMock(side_effect=conflict_error)

    mock_client.get = AsyncMock(side_effect=[first_list_response, second_list_response])
    mock_client.post = AsyncMock(
        side_effect=[create_response, by_ids_response, activate_response]
    )
    registry._client = mock_client

    result = await registry.sync_tools([_make_tool_registration()])

    assert result == {"add": "tool-existing"}
    assert mock_client.get.await_count == 2
    assert mock_client.post.await_count == 3
    assert (
        mock_client.post.await_args_list[0].args[0].endswith("/api/v1/tools/register")
    )
    assert mock_client.post.await_args_list[1].args[0].endswith("/api/v1/tools/by_ids")
    assert (
        mock_client.post.await_args_list[2]
        .args[0]
        .endswith("/api/v1/tools/tool-existing/activate")
    )


@pytest.mark.asyncio
async def test_sync_tools_raises_registration_error_on_transport_failure():
    from agent_sdk.layer1_domain.exceptions import RegistrationError
    from agent_sdk.layer4_frameworks.registry.http_tool_registry import HttpToolRegistry

    registry = HttpToolRegistry.__new__(HttpToolRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    registry._client = mock_client

    with pytest.raises(RegistrationError, match="refused"):
        await registry.sync_tools([_make_tool_registration()])


def test_itoolregistry_protocol_exposes_sync_tools_contract():
    import inspect

    from agent_sdk.layer2_application.interfaces.tool_registry import IToolRegistry

    assert hasattr(IToolRegistry, "sync_tools")
    method = getattr(IToolRegistry, "sync_tools")
    sig = inspect.signature(method)
    params = list(sig.parameters.keys())
    assert params == ["self", "registrations"]
    assert hasattr(IToolRegistry, "activate_tools")
    assert hasattr(IToolRegistry, "deactivate_tools")
    assert hasattr(IToolRegistry, "close")
