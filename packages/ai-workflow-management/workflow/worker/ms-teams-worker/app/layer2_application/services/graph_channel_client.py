"""Microsoft Graph client for Teams channel administration.

SA-1550. OAuth2 client-credentials (application permissions: ``Channel.Create``,
``TeamMember.ReadWrite.All``, ``Team.ReadBasic.All``). Token plumbing mirrors the
SA-1549 GraphMeetingClient / email-worker die. Ops: create_channel, add_member,
list_channels; plus list_teams for design-time pickers (consumed by the SA-1551
credential-aware-options enabler).
"""

from typing import Any, Dict, List, Optional

import httpx


class GraphChannelError(Exception):
    """Base exception for Graph channel operations."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class GraphAuthError(GraphChannelError):
    """Token endpoint rejected the client credentials (400/401/403)."""


class GraphPermissionError(GraphChannelError):
    """Graph returned 403 (missing application permission / consent)."""


class GraphNotFoundError(GraphChannelError):
    """Graph returned 404 (team not found)."""


class GraphConflictError(GraphChannelError):
    """Graph returned 409 (channel already exists)."""


class GraphRateLimitError(GraphChannelError):
    """Graph returned 429."""


class GraphChannelClient:
    """Teams channel admin via Microsoft Graph."""

    DEFAULT_SCOPE = "https://graph.microsoft.com/.default"

    def __init__(
        self,
        graph_base_url: str = "https://graph.microsoft.com/v1.0",
        timeout: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.graph_base_url = graph_base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport

    def fetch_token(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        scopes: str = "",
    ) -> str:
        """Fetch an access token via the OAuth2 client-credentials grant."""
        scope = (scopes or "").replace(",", " ").strip() or self.DEFAULT_SCOPE
        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        }
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                response = client.post(token_url, data=data)
        except httpx.TimeoutException as e:
            raise GraphChannelError(f"Token request timed out: {str(e)}", error_code="TIMEOUT") from e
        except httpx.HTTPError as e:
            raise GraphChannelError(f"HTTP error: {str(e)}", error_code="HTTP_ERROR") from e

        if response.status_code in (400, 401, 403):
            raise GraphAuthError(
                f"Token endpoint rejected the client credentials: HTTP {response.status_code}",
                status_code=response.status_code,
                error_code="AUTH_ERROR",
            )
        if response.status_code >= 400:
            raise GraphChannelError(
                f"Token endpoint returned HTTP {response.status_code}",
                status_code=response.status_code,
                error_code="TOKEN_ERROR",
            )
        payload = response.json()
        if "access_token" not in payload:
            raise GraphChannelError(
                "Token endpoint response missing access_token", error_code="TOKEN_ERROR"
            )
        return payload["access_token"]

    # -- request helper -----------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        access_token: str,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        url = f"{self.graph_base_url}{path}"
        headers = {"Authorization": f"Bearer {access_token}"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                return client.request(method, url, json=json_body, headers=headers)
        except httpx.TimeoutException as e:
            raise GraphChannelError(f"Request timed out: {str(e)}", error_code="TIMEOUT") from e
        except httpx.HTTPError as e:
            raise GraphChannelError(f"HTTP error: {str(e)}", error_code="HTTP_ERROR") from e

    def _raise_for_status(self, response: httpx.Response, context: str) -> None:
        code = response.status_code
        if code < 400:
            return
        if code == 403:
            raise GraphPermissionError(
                f"Graph denied {context} (403). The app needs the relevant application "
                "permission (Channel.Create / TeamMember.ReadWrite.All / Team.ReadBasic.All) "
                "with admin consent.",
                status_code=403, error_code="PERMISSION_MISSING",
            )
        if code == 404:
            raise GraphNotFoundError(
                f"{context}: team or resource not found (404).",
                status_code=404, error_code="TEAM_NOT_FOUND",
            )
        if code == 409:
            raise GraphConflictError(
                f"{context}: channel already exists (409).",
                status_code=409, error_code="CHANNEL_EXISTS",
            )
        if code == 429:
            raise GraphRateLimitError(
                f"{context}: rate limit exceeded (429).",
                status_code=429, error_code="RATE_LIMIT",
            )
        raise GraphChannelError(
            f"{context} failed with HTTP {code}", status_code=code, error_code="GRAPH_ERROR"
        )

    # -- operations ---------------------------------------------------------
    def create_channel(
        self,
        access_token: str,
        team_id: str,
        display_name: str,
        description: str = "",
        membership_type: str = "standard",
        owner_user_id: str = "",
    ) -> Dict[str, Any]:
        """Create a channel. Private channels require one owner member in the body."""
        body: Dict[str, Any] = {
            "displayName": display_name,
            "description": description,
            "membershipType": membership_type,
        }
        if membership_type == "private":
            if not owner_user_id:
                raise ValueError(
                    "VALIDATION_ERROR: a private channel requires 'owner_user_id'"
                )
            body["members"] = [{
                "@odata.type": "#microsoft.graph.aadUserConversationMember",
                "roles": ["owner"],
                "user@odata.bind": f"{self.graph_base_url}/users('{owner_user_id}')",
            }]
        r = self._request("POST", f"/teams/{team_id}/channels", access_token, body)
        self._raise_for_status(r, "create_channel")
        d = r.json()
        return {"success": True, "channel_id": d.get("id"), "web_url": d.get("webUrl")}

    def add_member(
        self,
        access_token: str,
        team_id: str,
        user_id: str,
        owner: bool = False,
    ) -> Dict[str, Any]:
        """Add a user to the team (aadUserConversationMember)."""
        body = {
            "@odata.type": "#microsoft.graph.aadUserConversationMember",
            "roles": ["owner"] if owner else [],
            "user@odata.bind": f"{self.graph_base_url}/users('{user_id}')",
        }
        r = self._request("POST", f"/teams/{team_id}/members", access_token, body)
        self._raise_for_status(r, "add_member")
        d = r.json()
        return {"success": True, "membership_id": d.get("id")}

    def list_channels(self, access_token: str, team_id: str) -> Dict[str, Any]:
        r = self._request("GET", f"/teams/{team_id}/channels", access_token)
        self._raise_for_status(r, "list_channels")
        d = r.json()
        return {
            "success": True,
            "channels": [
                {"id": c.get("id"), "displayName": c.get("displayName")}
                for c in d.get("value", [])
            ],
        }

    def list_teams(self, access_token: str) -> List[Dict[str, str]]:
        """List teams (design-time picker source; Team.ReadBasic.All)."""
        r = self._request("GET", "/teams?$select=id,displayName", access_token)
        self._raise_for_status(r, "list_teams")
        d = r.json()
        return [
            {"id": t.get("id"), "displayName": t.get("displayName")}
            for t in d.get("value", [])
        ]
