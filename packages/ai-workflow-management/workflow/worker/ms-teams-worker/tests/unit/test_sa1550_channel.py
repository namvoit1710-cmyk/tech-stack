"""SA-1550 — ms_teams_channel node + Graph channel client.

Covers: registry (all three node types), the channel handler (create/add/list,
validation, fail-closed auth, error mapping), design-time functions
(list_teams/list_channels), and GraphChannelClient status mapping.
"""

from unittest.mock import patch

import httpx
import pytest

from app import node_types as nt
from app.layer2_application.services.graph_channel_client import (
    GraphAuthError,
    GraphChannelClient,
    GraphChannelError,
    GraphConflictError,
    GraphNotFoundError,
    GraphPermissionError,
    GraphRateLimitError,
)


OAUTH_CRED = {"c1": {"type": "json", "config": {
    "tokenUrl": "https://login/oauth2/v2.0/token", "clientId": "cid", "clientSecret": "sec"}}}


def _inputs(op, **extra):
    base = {"credential_ref": "c1", "__credentials": OAUTH_CRED,
            "operation_type": op, "team_id": "team-1"}
    base.update(extra)
    return base


class TestRegistry:
    def test_registers_three_node_types(self):
        assert [d.worker_type for d in nt.NODE_TYPES] == [
            "ms_teams", "ms_teams_meeting", "ms_teams_channel"]

    def test_channel_node_shape(self):
        d = nt.MS_TEAMS_CHANNEL_NODE_DEF
        in_keys = {f["key"] for f in d.input_schema}
        assert {"operation_type", "team_id", "display_name", "user_id", "membership_type"} <= in_keys
        assert d.functions and {f.name for f in d.functions} == {"list_teams", "list_channels"}


class TestChannelHandler:
    def test_create_channel_standard(self):
        with patch.object(GraphChannelClient, "fetch_token", return_value="t"), \
             patch.object(GraphChannelClient, "create_channel", return_value={
                 "success": True, "channel_id": "ch-1", "web_url": "https://teams/ch-1"}) as mock:
            out = nt._channel_handler(_inputs("create_channel", display_name="CR War Room"), {})
        assert out["success"] is True and out["channel_id"] == "ch-1"
        assert mock.call_args.kwargs["membership_type"] == "standard"

    def test_create_channel_requires_display_name(self):
        with patch.object(GraphChannelClient, "fetch_token", return_value="t"):
            with pytest.raises(ValueError) as exc:
                nt._channel_handler(_inputs("create_channel"), {})
        assert "display_name" in str(exc.value)

    def test_add_member(self):
        with patch.object(GraphChannelClient, "fetch_token", return_value="t"), \
             patch.object(GraphChannelClient, "add_member", return_value={
                 "success": True, "membership_id": "m-9"}) as mock:
            out = nt._channel_handler(_inputs("add_member", user_id="u@x.com", owner=True), {})
        assert out["membership_id"] == "m-9"
        assert mock.call_args.kwargs["owner"] is True

    def test_list_channels(self):
        with patch.object(GraphChannelClient, "fetch_token", return_value="t"), \
             patch.object(GraphChannelClient, "list_channels", return_value={
                 "success": True, "channels": [{"id": "c1", "displayName": "General"}]}):
            out = nt._channel_handler(_inputs("list_channels"), {})
        assert out["channels"][0]["displayName"] == "General"

    def test_missing_team_id(self):
        inputs = _inputs("list_channels"); inputs["team_id"] = ""
        with pytest.raises(ValueError) as exc:
            nt._channel_handler(inputs, {})
        assert "team_id" in str(exc.value)

    def test_bad_operation(self):
        with pytest.raises(ValueError) as exc:
            nt._channel_handler(_inputs("nuke_everything"), {})
        assert "unsupported operation_type" in str(exc.value)

    def test_missing_credential_fails_closed(self):
        inputs = _inputs("list_channels"); inputs.pop("__credentials")
        with pytest.raises(ValueError):
            nt._channel_handler(inputs, {})

    def test_graph_error_maps_code(self):
        with patch.object(GraphChannelClient, "fetch_token", return_value="t"), \
             patch.object(GraphChannelClient, "create_channel",
                          side_effect=GraphConflictError("dup", status_code=409, error_code="CHANNEL_EXISTS")):
            with pytest.raises(RuntimeError) as exc:
                nt._channel_handler(_inputs("create_channel", display_name="dupe"), {})
        assert "CHANNEL_EXISTS" in str(exc.value)


class TestDesignTimeFunctions:
    @pytest.mark.asyncio
    async def test_list_teams_success(self):
        with patch.object(GraphChannelClient, "fetch_token", return_value="t"), \
             patch.object(GraphChannelClient, "list_teams", return_value=[{"id": "t1", "displayName": "Gov"}]):
            r = await nt.list_teams_fn({"credential_ref": "c1", "__credentials": OAUTH_CRED})
        assert r["success"] is True and r["options"][0]["id"] == "t1"

    @pytest.mark.asyncio
    async def test_list_channels_requires_team(self):
        r = await nt.list_channels_fn({"credential_ref": "c1", "__credentials": OAUTH_CRED})
        assert r["success"] is False and r["error_code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_list_channels_success(self):
        with patch.object(GraphChannelClient, "fetch_token", return_value="t"), \
             patch.object(GraphChannelClient, "list_channels", return_value={
                 "success": True, "channels": [{"id": "c1", "displayName": "General"}]}):
            r = await nt.list_channels_fn(
                {"credential_ref": "c1", "__credentials": OAUTH_CRED, "team_id": "team-1"})
        assert r["success"] is True and r["options"][0]["displayName"] == "General"

    @pytest.mark.asyncio
    async def test_list_teams_graph_error_returns_dict(self):
        with patch.object(GraphChannelClient, "fetch_token", return_value="t"), \
             patch.object(GraphChannelClient, "list_teams",
                          side_effect=GraphPermissionError("no", status_code=403, error_code="PERMISSION_MISSING")):
            r = await nt.list_teams_fn({"credential_ref": "c1", "__credentials": OAUTH_CRED})
        assert r["success"] is False and r["error_code"] == "PERMISSION_MISSING"


class TestGraphChannelClient:
    def _c(self, handler):
        return GraphChannelClient(transport=httpx.MockTransport(handler))

    def test_create_channel_standard(self):
        def h(req):
            return httpx.Response(201, json={"id": "ch-1", "webUrl": "https://teams/ch-1"})
        r = self._c(h).create_channel(access_token="t", team_id="tm", display_name="X")
        assert r["channel_id"] == "ch-1" and r["web_url"] == "https://teams/ch-1"

    def test_create_private_channel_requires_owner(self):
        c = self._c(lambda req: httpx.Response(201, json={"id": "x"}))
        with pytest.raises(ValueError) as exc:
            c.create_channel(access_token="t", team_id="tm", display_name="X", membership_type="private")
        assert "owner_user_id" in str(exc.value)

    def test_create_private_channel_with_owner_sends_members(self):
        seen = {}
        def h(req):
            import json as _json
            seen["body"] = _json.loads(req.content)
            return httpx.Response(201, json={"id": "chp", "webUrl": "w"})
        self._c(h).create_channel(access_token="t", team_id="tm", display_name="X",
                                  membership_type="private", owner_user_id="owner-1")
        assert seen["body"]["members"][0]["roles"] == ["owner"]
        assert "owner-1" in seen["body"]["members"][0]["user@odata.bind"]

    def test_add_member_owner_role(self):
        seen = {}
        def h(req):
            import json as _json
            seen["body"] = _json.loads(req.content)
            return httpx.Response(201, json={"id": "m1"})
        r = self._c(h).add_member(access_token="t", team_id="tm", user_id="u1", owner=True)
        assert r["membership_id"] == "m1" and seen["body"]["roles"] == ["owner"]

    def test_list_channels(self):
        def h(req):
            return httpx.Response(200, json={"value": [{"id": "c1", "displayName": "General"}]})
        r = self._c(h).list_channels(access_token="t", team_id="tm")
        assert r["channels"] == [{"id": "c1", "displayName": "General"}]

    def test_list_teams(self):
        def h(req):
            return httpx.Response(200, json={"value": [{"id": "t1", "displayName": "Gov"}]})
        r = self._c(h).list_teams(access_token="t")
        assert r == [{"id": "t1", "displayName": "Gov"}]

    def test_403_permission(self):
        c = self._c(lambda req: httpx.Response(403, json={}))
        with pytest.raises(GraphPermissionError) as exc:
            c.list_channels(access_token="t", team_id="tm")
        assert exc.value.error_code == "PERMISSION_MISSING"

    def test_404_team_not_found(self):
        c = self._c(lambda req: httpx.Response(404, json={}))
        with pytest.raises(GraphNotFoundError) as exc:
            c.create_channel(access_token="t", team_id="tm", display_name="X")
        assert exc.value.error_code == "TEAM_NOT_FOUND"

    def test_409_conflict(self):
        c = self._c(lambda req: httpx.Response(409, json={}))
        with pytest.raises(GraphConflictError):
            c.create_channel(access_token="t", team_id="tm", display_name="X")

    def test_429_rate_limit(self):
        c = self._c(lambda req: httpx.Response(429, json={}))
        with pytest.raises(GraphRateLimitError):
            c.list_channels(access_token="t", team_id="tm")

    def test_500_generic(self):
        c = self._c(lambda req: httpx.Response(500, json={}))
        with pytest.raises(GraphChannelError) as exc:
            c.list_channels(access_token="t", team_id="tm")
        assert exc.value.error_code == "GRAPH_ERROR"

    def test_token_auth_error(self):
        c = self._c(lambda req: httpx.Response(401, json={}))
        with pytest.raises(GraphAuthError):
            c.fetch_token("https://t/token", "cid", "bad")

    def test_token_success(self):
        c = self._c(lambda req: httpx.Response(200, json={"access_token": "tok"}))
        assert c.fetch_token("https://t/token", "cid", "sec") == "tok"

    def test_token_missing_access_token(self):
        c = self._c(lambda req: httpx.Response(200, json={"x": 1}))
        with pytest.raises(GraphChannelError) as exc:
            c.fetch_token("https://t/token", "cid", "sec")
        assert exc.value.error_code == "TOKEN_ERROR"

    def test_token_timeout(self):
        def h(req):
            raise httpx.TimeoutException("slow")
        with pytest.raises(GraphChannelError) as exc:
            self._c(h).fetch_token("https://t/token", "cid", "sec")
        assert exc.value.error_code == "TIMEOUT"

    def test_request_http_error(self):
        def h(req):
            raise httpx.ConnectError("boom")
        with pytest.raises(GraphChannelError) as exc:
            self._c(h).list_channels(access_token="t", team_id="tm")
        assert exc.value.error_code == "HTTP_ERROR"

    def test_token_500(self):
        c = self._c(lambda req: httpx.Response(500, json={}))
        with pytest.raises(GraphChannelError) as exc:
            c.fetch_token("https://t/token", "cid", "sec")
        assert exc.value.error_code == "TOKEN_ERROR"
