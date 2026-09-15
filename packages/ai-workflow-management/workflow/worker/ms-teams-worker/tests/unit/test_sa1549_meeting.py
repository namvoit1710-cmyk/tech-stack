"""SA-1549 — ms_teams_meeting node + Graph meeting client.

Covers: multi-type registry (ms_teams parity adapter + new meeting node), the
meeting handler (happy path, validation, fail-closed auth, Graph error mapping),
and the GraphMeetingClient (token + create_event status mapping).
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app import node_types as nt
from app.layer2_application.services.graph_meeting_client import (
    GraphAuthError,
    GraphMeetingClient,
    GraphMeetingError,
    GraphPermissionError,
    GraphRateLimitError,
)


OAUTH_CRED = {
    "cred-1": {
        "type": "json",
        "config": {
            "tokenUrl": "https://login.microsoftonline.com/t/oauth2/v2.0/token",
            "clientId": "client-abc",
            "clientSecret": "secret-xyz",
        },
    }
}


def _meeting_inputs(**extra):
    base = {
        "credential_ref": "cred-1",
        "__credentials": OAUTH_CRED,
        "organizer": "organizer@contoso.com",
        "subject": "Governance review",
        "start": "2026-07-10T09:00:00",
        "end": "2026-07-10T09:30:00",
    }
    base.update(extra)
    return base


# --------------------------------------------------------------------------
# Registry shape
# --------------------------------------------------------------------------
class TestNodeTypesRegistry:
    def test_registers_meeting_alongside_ms_teams(self):
        # SA-1550 adds ms_teams_channel; assert meeting + base are present and ordered.
        wts = [d.worker_type for d in nt.NODE_TYPES]
        assert wts[:2] == ["ms_teams", "ms_teams_meeting"]
        assert "ms_teams_meeting" in wts

    def test_ms_teams_node_preserves_identity(self):
        d = nt.MS_TEAMS_NODE_DEF
        assert d.worker_type == "ms_teams"
        assert d.node_class == "BUSINESS"
        keys = [f["key"] for f in d.input_schema]
        assert "mentions" in keys  # SA-1548 field carried through
        assert d.handler is nt._ms_teams_handler
        assert d.functions  # test_webhook preserved

    def test_meeting_node_schema(self):
        d = nt.MS_TEAMS_MEETING_NODE_DEF
        assert d.worker_type == "ms_teams_meeting"
        in_keys = {f["key"] for f in d.input_schema}
        assert {"organizer", "subject", "start", "end", "attendees"} <= in_keys
        out_keys = {f["key"] for f in d.output_schema}
        assert {"join_url", "event_id", "web_link"} <= out_keys


# --------------------------------------------------------------------------
# ms_teams parity adapter
# --------------------------------------------------------------------------
class TestMsTeamsAdapter:
    def test_adapter_runs_webhook_success(self):
        inputs = {
            "credential_ref": "c1",
            "__credentials": {"c1": {"type": "webhook_url", "config": {
                "url": "https://prod-1.logic.azure.com/workflows/ok"}}},
            "operation_type": "send_message",
            "message_content": "hi",
        }
        with patch("app.layer2_application.services.webhook_client.TeamsWebhookClient.send_message",
                   return_value={"success": True, "response": "1"}):
            out = nt._ms_teams_handler(inputs, {})
        assert out["success"] is True

    def test_adapter_raises_with_error_code_on_failure(self):
        # retired URL → use case returns error with O365_CONNECTOR_RETIRED
        inputs = {
            "credential_ref": "c1",
            "__credentials": {"c1": {"type": "webhook_url", "config": {
                "url": "https://contoso.webhook.office.com/webhookb2/x/IncomingWebhook/y"}}},
            "operation_type": "send_message",
            "message_content": "hi",
        }
        with pytest.raises(RuntimeError) as exc:
            nt._ms_teams_handler(inputs, {})
        assert "O365_CONNECTOR_RETIRED" in str(exc.value)


# --------------------------------------------------------------------------
# meeting handler
# --------------------------------------------------------------------------
class TestMeetingHandler:
    def test_happy_path_returns_join_url(self):
        with patch.object(GraphMeetingClient, "fetch_token", return_value="tok"), \
             patch.object(GraphMeetingClient, "create_event", return_value={
                 "success": True, "event_id": "evt-1",
                 "join_url": "https://teams.microsoft.com/l/meetup-join/xyz",
                 "web_link": "https://outlook.office.com/calendar/evt-1",
             }) as mock_create:
            out = nt._meeting_handler(_meeting_inputs(attendees=["a@x.com", "b@y.com"]), {})
        assert out["success"] is True
        assert out["join_url"].startswith("https://teams.microsoft.com")
        assert out["event_id"] == "evt-1"
        # attendees parsed and passed through
        assert mock_create.call_args.kwargs["attendees"] == ["a@x.com", "b@y.com"]

    @pytest.mark.parametrize("missing", ["organizer", "subject", "start", "end"])
    def test_validation_errors(self, missing):
        inputs = _meeting_inputs()
        inputs[missing] = ""
        with pytest.raises(ValueError) as exc:
            nt._meeting_handler(inputs, {})
        assert "VALIDATION_ERROR" in str(exc.value)

    def test_missing_credential_fails_closed(self):
        inputs = _meeting_inputs()
        inputs.pop("__credentials")
        with pytest.raises(ValueError):
            nt._meeting_handler(inputs, {})

    def test_graph_permission_error_maps_code(self):
        with patch.object(GraphMeetingClient, "fetch_token", return_value="tok"), \
             patch.object(GraphMeetingClient, "create_event",
                          side_effect=GraphPermissionError("no perm", status_code=403,
                                                            error_code="PERMISSION_MISSING")):
            with pytest.raises(RuntimeError) as exc:
                nt._meeting_handler(_meeting_inputs(), {})
        assert "PERMISSION_MISSING" in str(exc.value)

    def test_bad_credential_type_rejected(self):
        inputs = _meeting_inputs()
        inputs["__credentials"] = {"cred-1": {"type": "basic", "config": {"username": "u"}}}
        with pytest.raises(ValueError) as exc:
            nt._meeting_handler(inputs, {})
        assert "Microsoft Graph" in str(exc.value)

    def test_attendees_accepts_dict_rows(self):
        with patch.object(GraphMeetingClient, "fetch_token", return_value="tok"), \
             patch.object(GraphMeetingClient, "create_event", return_value={
                 "success": True, "event_id": "e", "join_url": "j", "web_link": "w"}) as mock_create:
            nt._meeting_handler(_meeting_inputs(attendees=[{"email": "c@z.com"}, {"0": "d@z.com"}]), {})
        assert mock_create.call_args.kwargs["attendees"] == ["c@z.com", "d@z.com"]


# --------------------------------------------------------------------------
# GraphMeetingClient
# --------------------------------------------------------------------------
class TestGraphMeetingClient:
    def _client_with(self, handler):
        return GraphMeetingClient(transport=httpx.MockTransport(handler))

    def test_fetch_token_success(self):
        def handler(req):
            return httpx.Response(200, json={"access_token": "tok-123"})
        c = self._client_with(handler)
        assert c.fetch_token("https://t/token", "cid", "sec") == "tok-123"

    def test_fetch_token_auth_error(self):
        def handler(req):
            return httpx.Response(401, json={"error": "invalid_client"})
        c = self._client_with(handler)
        with pytest.raises(GraphAuthError) as exc:
            c.fetch_token("https://t/token", "cid", "bad")
        assert exc.value.error_code == "AUTH_ERROR"

    def test_create_event_success(self):
        def handler(req):
            return httpx.Response(201, json={
                "id": "evt-9", "webLink": "https://outlook/evt-9",
                "onlineMeeting": {"joinUrl": "https://teams/join/9"},
            })
        c = self._client_with(handler)
        r = c.create_event(access_token="t", organizer="o@x.com", subject="s",
                           start="2026-07-10T09:00:00", end="2026-07-10T09:30:00")
        assert r["success"] is True
        assert r["join_url"] == "https://teams/join/9"
        assert r["event_id"] == "evt-9"

    def test_create_event_sends_online_meeting_flag(self):
        seen = {}
        def handler(req):
            import json as _json
            seen["body"] = _json.loads(req.content)
            return httpx.Response(201, json={"id": "e", "onlineMeeting": {"joinUrl": "j"}})
        c = self._client_with(handler)
        c.create_event(access_token="t", organizer="o@x.com", subject="s",
                       start="s1", end="s2", attendees=["a@b.com"])
        assert seen["body"]["isOnlineMeeting"] is True
        assert seen["body"]["onlineMeetingProvider"] == "teamsForBusiness"
        assert seen["body"]["attendees"][0]["emailAddress"]["address"] == "a@b.com"

    def test_create_event_403_permission(self):
        c = self._client_with(lambda req: httpx.Response(403, json={}))
        with pytest.raises(GraphPermissionError) as exc:
            c.create_event(access_token="t", organizer="o", subject="s", start="a", end="b")
        assert exc.value.error_code == "PERMISSION_MISSING"

    def test_create_event_429_rate_limit(self):
        c = self._client_with(lambda req: httpx.Response(429, json={}))
        with pytest.raises(GraphRateLimitError):
            c.create_event(access_token="t", organizer="o", subject="s", start="a", end="b")

    def test_create_event_404_organizer(self):
        c = self._client_with(lambda req: httpx.Response(404, json={}))
        with pytest.raises(GraphMeetingError) as exc:
            c.create_event(access_token="t", organizer="o", subject="s", start="a", end="b")
        assert exc.value.error_code == "ORGANIZER_NOT_FOUND"

    def test_create_event_500_generic(self):
        c = self._client_with(lambda req: httpx.Response(500, json={}))
        with pytest.raises(GraphMeetingError) as exc:
            c.create_event(access_token="t", organizer="o", subject="s", start="a", end="b")
        assert exc.value.error_code == "GRAPH_ERROR"

    def test_fetch_token_500_token_error(self):
        c = self._client_with(lambda req: httpx.Response(500, json={}))
        with pytest.raises(GraphMeetingError) as exc:
            c.fetch_token("https://t/token", "cid", "sec")
        assert exc.value.error_code == "TOKEN_ERROR"

    def test_fetch_token_missing_access_token(self):
        c = self._client_with(lambda req: httpx.Response(200, json={"nope": 1}))
        with pytest.raises(GraphMeetingError) as exc:
            c.fetch_token("https://t/token", "cid", "sec")
        assert exc.value.error_code == "TOKEN_ERROR"

    def test_fetch_token_timeout_wrapped(self):
        def handler(req):
            raise httpx.TimeoutException("slow")
        c = self._client_with(handler)
        with pytest.raises(GraphMeetingError) as exc:
            c.fetch_token("https://t/token", "cid", "sec")
        assert exc.value.error_code == "TIMEOUT"

    def test_create_event_http_error_wrapped(self):
        def handler(req):
            raise httpx.ConnectError("boom")
        c = self._client_with(handler)
        with pytest.raises(GraphMeetingError) as exc:
            c.create_event(access_token="t", organizer="o", subject="s", start="a", end="b")
        assert exc.value.error_code == "HTTP_ERROR"
