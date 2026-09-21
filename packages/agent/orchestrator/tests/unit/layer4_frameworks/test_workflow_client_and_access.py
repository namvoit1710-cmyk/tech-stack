from unittest.mock import AsyncMock, MagicMock

import pytest
import httpx


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {"content-type": "application/json"}
        self.content = text.encode("utf-8") if text else (b"{}" if payload is not None else b"")
        self.text = text or ("{}" if payload is not None else "")

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload



# @pytest.mark.asyncio
# async def test_agent_registry_client_filters_and_handles_failures(monkeypatch):
#     from app.layer4_frameworks.clients.agent_registry_client import AgentRegistryClient

#     logger = MagicMock()
#     client = AgentRegistryClient("http://registry", logger=logger)

#     async def request_success(method, path, **kwargs):
#         assert kwargs["params"] == {"status": "ACTIVE"}
#         return _FakeResponse(
#             payload={
#                 "agents": [
#                     {
#                         "id": "agent-1",
#                         "name": "Agent One",
#                         "kind": "BUSINESS",
#                         "status": "ACTIVE",
#                         "is_published": True,
#                     }
#                 ]
#             }
#         )

#     client._client.request = request_success
#     agents = await client.filter_agents(status="ACTIVE")
#     assert [agent.id for agent in agents] == ["agent-1"]

#     client._client.request = AsyncMock(return_value=_FakeResponse(status_code=404))
#     assert await client._get("/missing") is None

#     async def request_fail(method, path, **kwargs):
#         raise httpx.ConnectError("down")

#     client._client.request = request_fail

#     async def _sleep(*_args, **_kwargs):
#         return None

#     monkeypatch.setattr("app.layer4_frameworks.clients.agent_registry_client.asyncio.sleep", _sleep)
#     for _ in range(3):
#         assert await client._request("GET", "/agents") is None

#     assert client._circuit_open_until > 0
#     logger.error.assert_called_once()
#     await client.close()


@pytest.mark.asyncio
async def test_agent_registry_client_get_available_agents_returns_empty_when_missing():
    from app.layer4_frameworks.clients.agent_registry_client import AgentRegistryClient

    client = AgentRegistryClient("http://registry", logger=MagicMock())
    client._get = AsyncMock(return_value=None)

    result = await client.get_available_agents()

    assert result == []
    await client.close()


# --- SA-892: get_tool_id_by_name (discover a tool id by metadata.tool_name) ----
#
# The registry has no by-name endpoint. GET /tools/all returns SUMMARY rows whose
# ``name`` is a slug (agent__<owner>__<tool>__<ver>) with NO metadata.tool_name; the
# authoritative tool name lives in the FULL record (POST /tools/by_ids) under
# ``metadata.tool_name`` — the same value the executor's resolver maps to the local
# tool. So get_tool_id_by_name: /tools/all (active ids) -> /tools/by_ids -> match
# metadata.tool_name == name (prefer an executor-runtime owner). It uses its OWN
# short-lived httpx.AsyncClient, so we fake that class.


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _install_fake_httpx(monkeypatch, *, all_payload, byids_payload=None, get_error=None):
    """Patch httpx.AsyncClient used inside get_tool_id_by_name. Records calls."""
    holder = {"gets": [], "posts": [], "clients": 0}

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            holder["clients"] += 1

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, path):
            holder["gets"].append(path)
            if get_error is not None:
                raise get_error
            return _FakeResp(all_payload)

        async def post(self, path, json=None):
            holder["posts"].append((path, json))
            return _FakeResp(byids_payload)

        async def aclose(self):
            return None

    monkeypatch.setattr(
        "app.layer4_frameworks.clients.agent_registry_client.httpx.AsyncClient",
        _FakeAsyncClient,
    )
    return holder


@pytest.mark.asyncio
async def test_get_tool_id_by_name_matches_metadata_tool_name_and_caches(monkeypatch):
    """Matches on metadata.tool_name (NOT the slug name), and caches so a second
    lookup does not re-query /tools/all."""
    from app.layer4_frameworks.clients.agent_registry_client import AgentRegistryClient

    holder = _install_fake_httpx(
        monkeypatch,
        all_payload={
            "tools": [
                {"id": "id-echo", "name": "agent__x__echo__0.1.0", "status": "active"},
                {
                    "id": "id-stop",
                    "name": "agent__executor-runtime__workflow_stop__0.1.1",
                    "status": "active",
                },
            ]
        },
        byids_payload={
            "tools": [
                {"id": "id-echo", "name": "agent__x__echo__0.1.0",
                 "metadata": {"tool_name": "echo"}},
                {"id": "id-stop", "name": "agent__executor-runtime__workflow_stop__0.1.1",
                 "metadata": {"tool_name": "workflow_stop"}},
            ]
        },
    )
    client = AgentRegistryClient("http://registry", logger=MagicMock())

    first = await client.get_tool_id_by_name("workflow_stop")
    second = await client.get_tool_id_by_name("workflow_stop")

    assert first == "id-stop"
    assert second == "id-stop"
    # by_ids was called with the ACTIVE ids from /tools/all
    assert holder["posts"] and holder["posts"][0][0] == "/api/v1/tools/by_ids"
    assert set(holder["posts"][0][1]) == {"id-echo", "id-stop"}
    # second call is cached -> only one /tools/all GET total
    assert holder["gets"].count("/api/v1/tools/all") == 1


@pytest.mark.asyncio
async def test_get_tool_id_by_name_prefers_executor_runtime_owner(monkeypatch):
    """When several records share metadata.tool_name, prefer the executor-runtime owner."""
    from app.layer4_frameworks.clients.agent_registry_client import AgentRegistryClient

    _install_fake_httpx(
        monkeypatch,
        all_payload={
            "tools": [
                {"id": "id-exec", "name": "agent__executor__workflow_stop__0.1.0", "status": "active"},
                {"id": "id-rt", "name": "agent__executor-runtime__workflow_stop__0.1.1", "status": "active"},
            ]
        },
        byids_payload={
            "tools": [
                {"id": "id-exec", "name": "agent__executor__workflow_stop__0.1.0",
                 "metadata": {"tool_name": "workflow_stop"}},
                {"id": "id-rt", "name": "agent__executor-runtime__workflow_stop__0.1.1",
                 "metadata": {"tool_name": "workflow_stop"}},
            ]
        },
    )
    client = AgentRegistryClient("http://registry", logger=MagicMock())

    assert await client.get_tool_id_by_name("workflow_stop") == "id-rt"


@pytest.mark.asyncio
async def test_get_tool_id_by_name_none_when_no_metadata_match(monkeypatch):
    """No full record has metadata.tool_name == name -> None (slug name is ignored)."""
    from app.layer4_frameworks.clients.agent_registry_client import AgentRegistryClient

    _install_fake_httpx(
        monkeypatch,
        all_payload={"tools": [
            {"id": "id-1", "name": "agent__x__workflow_stop__0.1.0", "status": "active"}
        ]},
        # slug says workflow_stop but the authoritative metadata.tool_name does NOT match
        byids_payload={"tools": [
            {"id": "id-1", "name": "agent__x__workflow_stop__0.1.0",
             "metadata": {"tool_name": "something_else"}}
        ]},
    )
    client = AgentRegistryClient("http://registry", logger=MagicMock())

    assert await client.get_tool_id_by_name("workflow_stop") is None


@pytest.mark.asyncio
async def test_get_tool_id_by_name_none_when_no_active_ids(monkeypatch):
    """No ACTIVE tools -> None, and by_ids is never called."""
    from app.layer4_frameworks.clients.agent_registry_client import AgentRegistryClient

    holder = _install_fake_httpx(
        monkeypatch,
        all_payload={"tools": [
            {"id": "id-x", "name": "agent__x__workflow_stop__0.1.0", "status": "deleted"}
        ]},
    )
    client = AgentRegistryClient("http://registry", logger=MagicMock())

    assert await client.get_tool_id_by_name("workflow_stop") is None
    assert holder["posts"] == []


@pytest.mark.asyncio
async def test_get_tool_id_by_name_none_on_error_or_empty(monkeypatch):
    """Registry unreachable (get raises) -> None, never raises; empty name -> None
    without any HTTP call."""
    from app.layer4_frameworks.clients.agent_registry_client import AgentRegistryClient

    holder = _install_fake_httpx(
        monkeypatch, all_payload=None, get_error=RuntimeError("registry down")
    )
    client = AgentRegistryClient("http://registry", logger=MagicMock())

    assert await client.get_tool_id_by_name("workflow_stop") is None  # error swallowed
    assert await client.get_tool_id_by_name("") is None  # short-circuit, no HTTP
    assert holder["gets"] == ["/api/v1/tools/all"]  # only the non-empty lookup hit HTTP


# def test_user_permission_provider_builds_urls_and_parses_payloads(monkeypatch):
#     from app.layer4_frameworks.security.user_permission_provider import UserPermissionProvider

#     provider = UserPermissionProvider("http://profile/")

#     assert provider._build_current_user_features_url() == "http://profile/api/v1/features/by-current-user"

#     response = _FakeResponse(payload=[{"id": "feature-1"}], text='[{"id": "feature-1"}]')
#     assert provider._parse_feature_payload(response, "http://profile") == [{"id": "feature-1"}]

#     no_content = _FakeResponse(status_code=204, payload=None, text="")
#     assert provider._parse_feature_payload(no_content, "http://profile") is None

#     html = _FakeResponse(status_code=200, payload=None, headers={"content-type": "text/html"}, text="<html>oops</html>")
#     assert provider._parse_feature_payload(html, "http://profile") is None


def test_user_permission_provider_fetches_features_and_handles_errors(monkeypatch):
    from app.layer4_frameworks.security.user_permission_provider import UserPermissionProvider

    provider = UserPermissionProvider("http://profile")

    monkeypatch.setattr(
        "app.layer4_frameworks.security.user_permission_provider.httpx.get",
        lambda *args, **kwargs: _FakeResponse(payload={"features": ["a"], "feature_flags": {"beta": True}}, text='{"features": ["a"], "feature_flags": {"beta": true}}'),
    )
    assert provider.get_user_features("user@example.com", "Bearer token") == {
        "email": "user@example.com",
        "features": ["a"],
        "feature_flags": {"beta": True},
    }

    monkeypatch.setattr(
        "app.layer4_frameworks.security.user_permission_provider.httpx.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.HTTPError("boom")),
    )
    assert provider._fetch_current_user_features("Bearer token") == {}
    assert provider.get_user_features("user@example.com", "Basic token") == {}