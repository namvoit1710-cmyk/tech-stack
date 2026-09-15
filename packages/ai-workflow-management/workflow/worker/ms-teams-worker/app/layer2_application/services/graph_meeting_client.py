"""Microsoft Graph client for creating Teams meetings (calendar events).

SA-1549. Uses the OAuth2 client-credentials flow (application permission
``Calendars.ReadWrite``) to create a calendar event with ``isOnlineMeeting=true`` —
this yields a Teams join URL without the ``/onlineMeetings`` endpoint's tenant
``CsApplicationAccessPolicy`` requirement. The token plumbing mirrors the shipped
email-worker ``GraphEmailClient`` (SA-1549 replay of the email-worker die).
"""

from typing import Any, Dict, List, Optional

import httpx


class GraphMeetingError(Exception):
    """Base exception for Graph meeting operations."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class GraphAuthError(GraphMeetingError):
    """Raised when the token endpoint rejects the client credentials (400/401/403)."""


class GraphPermissionError(GraphMeetingError):
    """Raised when Graph returns 403 (missing application permission / consent)."""


class GraphRateLimitError(GraphMeetingError):
    """Raised when Graph returns 429."""


class GraphMeetingClient:
    """Create Teams meetings via Microsoft Graph calendar events."""

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
        """Fetch an access token via the OAuth2 client-credentials grant.

        Raises:
            GraphAuthError: If the token endpoint rejects the client credentials.
            GraphMeetingError: For any other token endpoint failure.
        """
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
            raise GraphMeetingError(f"Token request timed out: {str(e)}", error_code="TIMEOUT") from e
        except httpx.HTTPError as e:
            raise GraphMeetingError(f"HTTP error: {str(e)}", error_code="HTTP_ERROR") from e

        if response.status_code in (400, 401, 403):
            # Do not echo the provider body — it can carry sensitive detail.
            raise GraphAuthError(
                f"Token endpoint rejected the client credentials: HTTP {response.status_code}",
                status_code=response.status_code,
                error_code="AUTH_ERROR",
            )
        if response.status_code >= 400:
            raise GraphMeetingError(
                f"Token endpoint returned HTTP {response.status_code}",
                status_code=response.status_code,
                error_code="TOKEN_ERROR",
            )

        payload = response.json()
        if "access_token" not in payload:
            raise GraphMeetingError(
                "Token endpoint response missing access_token", error_code="TOKEN_ERROR"
            )
        return payload["access_token"]

    def create_event(
        self,
        access_token: str,
        organizer: str,
        subject: str,
        start: str,
        end: str,
        time_zone: str = "UTC",
        body: str = "",
        attendees: Optional[List[str]] = None,
        allow_new_time_proposals: bool = True,
    ) -> Dict[str, Any]:
        """Create a calendar event with an online Teams meeting.

        Args:
            access_token: Bearer token for Graph.
            organizer: Mailbox to create the event under (UPN or user id).
            subject: Meeting subject.
            start: ISO 8601 start datetime (no offset; paired with time_zone).
            end: ISO 8601 end datetime.
            time_zone: IANA/Windows time zone name.
            body: Optional HTML/text body content.
            attendees: Optional list of attendee e-mail addresses (required type).
            allow_new_time_proposals: Graph flag.

        Returns:
            Dict with success, event_id, join_url, web_link.

        Raises:
            GraphPermissionError / GraphRateLimitError / GraphMeetingError.
        """
        url = f"{self.graph_base_url}/users/{organizer}/events"
        payload: Dict[str, Any] = {
            "subject": subject,
            "isOnlineMeeting": True,
            "onlineMeetingProvider": "teamsForBusiness",
            "start": {"dateTime": start, "timeZone": time_zone},
            "end": {"dateTime": end, "timeZone": time_zone},
            "allowNewTimeProposals": allow_new_time_proposals,
        }
        if body:
            payload["body"] = {"contentType": "HTML", "content": body}
        if attendees:
            payload["attendees"] = [
                {"emailAddress": {"address": a}, "type": "required"}
                for a in attendees
                if a
            ]

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            raise GraphMeetingError(f"Request timed out: {str(e)}", error_code="TIMEOUT") from e
        except httpx.HTTPError as e:
            raise GraphMeetingError(f"HTTP error: {str(e)}", error_code="HTTP_ERROR") from e

        if response.status_code in (200, 201):
            data = response.json()
            online = data.get("onlineMeeting") or {}
            return {
                "success": True,
                "event_id": data.get("id"),
                "join_url": online.get("joinUrl"),
                "web_link": data.get("webLink"),
            }

        if response.status_code == 403:
            raise GraphPermissionError(
                "Graph denied the request (403). The app registration needs the "
                "Calendars.ReadWrite application permission with admin consent.",
                status_code=403,
                error_code="PERMISSION_MISSING",
            )
        if response.status_code == 404:
            raise GraphMeetingError(
                f"Organizer mailbox '{organizer}' not found (404).",
                status_code=404,
                error_code="ORGANIZER_NOT_FOUND",
            )
        if response.status_code == 429:
            raise GraphRateLimitError(
                "Graph rate limit exceeded (429). Retry later.",
                status_code=429,
                error_code="RATE_LIMIT",
            )
        raise GraphMeetingError(
            f"Graph create-event failed with HTTP {response.status_code}",
            status_code=response.status_code,
            error_code="GRAPH_ERROR",
        )
