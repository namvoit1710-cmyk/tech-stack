"""MS Teams Webhook client for sending messages and cards."""

from typing import Any, Dict, List, Optional

import httpx


class WebhookError(Exception):
    """Base exception for webhook errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class WebhookNotFoundError(WebhookError):
    """Raised when webhook URL is invalid or not found (404)."""

    pass


class WebhookRateLimitError(WebhookError):
    """Raised when rate limit is exceeded (429)."""

    def __init__(
        self,
        message: str,
        retry_after: Optional[int] = None,
        status_code: int = 429,
        error_code: Optional[str] = None,
    ):
        super().__init__(message, status_code, error_code)
        self.retry_after = retry_after


class WebhookRetiredError(WebhookError):
    """Raised for O365 connector webhook URLs (service retired May 2026)."""

    def __init__(self, message: str):
        super().__init__(message, status_code=None, error_code="O365_CONNECTOR_RETIRED")


# O365 connector webhooks (\*.webhook.office.com) were retired by Microsoft in
# May 2026; posts to them fail. The replacement is a Teams "Workflows"
# (Power Automate) webhook.
RETIRED_WEBHOOK_HOST_SUFFIX = ".webhook.office.com"

RETIRED_WEBHOOK_MESSAGE = (
    "This webhook URL points to a retired O365 connector (*.webhook.office.com; "
    "service ended May 2026). Create a new webhook via Teams > Workflows "
    "(Power Automate) and update the credential."
)


def webhook_url_from_credential(cred: Optional[Dict[str, Any]]) -> str:
    """Extract the webhook URL from a resolved `{type, config}` credential.

    ONE reader for the vault credential shape, shared by the execute path and the
    design-time `test_webhook` function. They previously disagreed about where the
    URL lives — the execute path read the vault credential, the design-time
    function read an inline `webhook_url` param — which is the same class of
    drift that made a correctly-configured credential fail. Returns `""` when the
    credential is absent or is not a `webhook_url` kind; callers decide the error.
    """
    if not cred or cred.get("type") != "webhook_url":
        return ""
    return ((cred.get("config") or {}).get("url") or "").strip()


class TeamsWebhookClient:
    """Client for MS Teams Incoming Webhook operations.

    This client sends messages and Adaptive Cards to MS Teams channels
    via Incoming Webhooks. No authentication is required - the webhook
    URL itself serves as the authentication mechanism.
    """

    def __init__(self, timeout: float = 30.0):
        """Initialize the webhook client.

        Args:
            timeout: Default timeout for HTTP requests in seconds.
        """
        self.timeout = timeout

    @classmethod
    def validate_webhook_url(cls, webhook_url: str) -> None:
        """Reject retired O365 connector webhook URLs (fail fast, no silent drop).

        Both historical O365 connector shapes are dead:
        ``https://<tenant>.webhook.office.com/webhookb2/...`` and the older
        ``https://outlook.office.com/webhook/...``.

        Args:
            webhook_url: The webhook URL to validate.

        Raises:
            WebhookRetiredError: If the URL targets the retired connector service.
        """
        if not webhook_url:
            return
        url = httpx.URL(webhook_url)
        host = (url.host or "").lower()
        if host.endswith(RETIRED_WEBHOOK_HOST_SUFFIX):
            raise WebhookRetiredError(RETIRED_WEBHOOK_MESSAGE)
        if host == "outlook.office.com" and url.path.startswith("/webhook"):
            raise WebhookRetiredError(RETIRED_WEBHOOK_MESSAGE)

    def send_message(
        self,
        webhook_url: str,
        content: str,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a text message to a Teams channel via webhook.

        Args:
            webhook_url: The Incoming Webhook URL.
            content: Message content (supports markdown).
            title: Optional title for the message.

        Returns:
            Response indicating success.

        Raises:
            WebhookError: If the request fails.
        """
        # MessageCard format for simple messages
        payload: Dict[str, Any] = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "text": content,
        }
        if title:
            payload["title"] = title

        return self._send_to_webhook(webhook_url, payload)

    def send_adaptive_card(
        self,
        webhook_url: str,
        card_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Send an Adaptive Card to a Teams channel via webhook.

        Args:
            webhook_url: The Incoming Webhook URL.
            card_payload: The Adaptive Card JSON payload.

        Returns:
            Response indicating success.

        Raises:
            WebhookError: If the request fails.
        """
        # Wrap Adaptive Card in the required message format
        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": card_payload,
                }
            ],
        }
        return self._send_to_webhook(webhook_url, payload)

    def test_webhook(self, webhook_url: str) -> Dict[str, Any]:
        """Test if a webhook URL is valid by sending a test message.

        Args:
            webhook_url: The Incoming Webhook URL to test.

        Returns:
            Dictionary with success status and latency.
        """
        import time

        start = time.monotonic()
        try:
            # Send a minimal test message
            payload = {
                "@type": "MessageCard",
                "@context": "http://schema.org/extensions",
                "text": "🔗 Webhook connection test successful",
            }
            self._send_to_webhook(webhook_url, payload)
            latency_ms = (time.monotonic() - start) * 1000

            return {
                "success": True,
                "latency_ms": round(latency_ms, 2),
                "error": None,
            }
        except WebhookError as e:
            latency_ms = (time.monotonic() - start) * 1000
            return {
                "success": False,
                "latency_ms": round(latency_ms, 2),
                "error": str(e),
            }

    def _send_to_webhook(
        self,
        webhook_url: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Send a payload to the webhook URL.

        Args:
            webhook_url: The Incoming Webhook URL.
            payload: JSON payload to send.

        Returns:
            Response data.

        Raises:
            WebhookError: If the request fails.
        """
        self.validate_webhook_url(webhook_url)

        headers = {"Content-Type": "application/json"}

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(webhook_url, json=payload, headers=headers)

            # ANY 2xx means the message was accepted.
            #
            # Retired O365 connectors answered 200 with the body "1", and this
            # method used to match only that. Their replacement — a Teams
            # "Workflows" (Power Automate) webhook — answers 202 Accepted with an
            # EMPTY body, because the flow is triggered asynchronously and there is
            # no synchronous delivery receipt to wait for. Matching 200 alone sent
            # every Power Automate webhook down the generic failure branch below,
            # so a message that had in fact been accepted was reported to the user
            # as request_failed. Same trap as the retirement itself: the connector
            # changed, the success contract changed with it.
            if 200 <= response.status_code < 300:
                return {"success": True, "response": response.text}

            if response.status_code == 400:
                raise WebhookError(
                    f"Bad request: {response.text}",
                    status_code=400,
                    error_code="bad_request",
                )

            if response.status_code == 404:
                raise WebhookNotFoundError(
                    "Webhook URL not found. Please verify the URL is correct.",
                    status_code=404,
                    error_code="webhook_not_found",
                )

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                raise WebhookRateLimitError(
                    "Rate limit exceeded. Please try again later.",
                    retry_after=int(retry_after) if retry_after else None,
                    error_code="rate_limit",
                )

            raise WebhookError(
                f"Request failed with status {response.status_code}: {response.text}",
                status_code=response.status_code,
                error_code="request_failed",
            )

        except httpx.TimeoutException as e:
            raise WebhookError(
                f"Request timed out: {str(e)}",
                error_code="timeout",
            ) from e
        except httpx.HTTPError as e:
            raise WebhookError(
                f"HTTP error: {str(e)}",
                error_code="http_error",
            ) from e

    @classmethod
    def build_adaptive_card(
        cls,
        title: str,
        body: str,
        actions: Optional[List[Dict[str, str]]] = None,
        mentions: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Build an Adaptive Card payload.

        Args:
            title: Card title.
            body: Card body text (supports markdown).
            actions: List of action buttons, each with 'label' and 'url'.
            mentions: List of users to @mention, each with 'name' (display) and
                'id' (UPN e-mail or AAD object id). Rendered as a trailing
                TextBlock of ``<at>name</at>`` tags backed by
                ``msteams.entities`` mention entries — the shape Teams requires
                for a working notification.

        Returns:
            Adaptive Card JSON structure.
        """
        card: Dict[str, Any] = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {
                    "type": "TextBlock",
                    "text": title,
                    "weight": "Bolder",
                    "size": "Large",
                    "wrap": True,
                },
                {
                    "type": "TextBlock",
                    "text": body,
                    "wrap": True,
                },
            ],
        }

        valid_mentions = [
            m for m in (mentions or [])
            if isinstance(m, dict) and m.get("name") and m.get("id")
        ]
        if valid_mentions:
            card["body"].append(
                {
                    "type": "TextBlock",
                    "text": " ".join(f"<at>{m['name']}</at>" for m in valid_mentions),
                    "wrap": True,
                }
            )
            card["msteams"] = {
                "entities": [
                    {
                        "type": "mention",
                        "text": f"<at>{m['name']}</at>",
                        "mentioned": {"id": m["id"], "name": m["name"]},
                    }
                    for m in valid_mentions
                ]
            }

        if actions:
            card["actions"] = [
                {
                    "type": "Action.OpenUrl",
                    "title": action.get("label", "Open"),
                    "url": action.get("url", ""),
                }
                for action in actions
                if action.get("url")
            ]

        return card
