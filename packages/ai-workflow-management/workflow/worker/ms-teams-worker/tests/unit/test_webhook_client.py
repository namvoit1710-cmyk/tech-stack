"""Tests for TeamsWebhookClient."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.layer2_application.services.webhook_client import (
    TeamsWebhookClient,
    WebhookError,
    WebhookNotFoundError,
    WebhookRateLimitError,
)


class TestTeamsWebhookClientSendMessage:
    """Tests for send_message method."""

    @pytest.fixture
    def client(self):
        return TeamsWebhookClient()

    def test_send_message_success(self, client):
        """Test successful message sending."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "1"

        with patch.object(httpx.Client, "post", return_value=mock_response):
            result = client.send_message(
                webhook_url="https://prod-42.westus.logic.azure.com/workflows/test",
                content="Hello Teams!",
            )

        assert result["success"] is True
        assert result["response"] == "1"

    def test_send_message_with_title(self, client):
        """Test message with title."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "1"

        with patch.object(httpx.Client, "post", return_value=mock_response) as mock_post:
            result = client.send_message(
                webhook_url="https://prod-42.westus.logic.azure.com/workflows/test",
                content="Hello Teams!",
                title="Test Title",
            )

        assert result["success"] is True
        # Verify payload includes title
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["title"] == "Test Title"

    def test_send_message_not_found(self, client):
        """Test 404 error handling."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"

        with patch.object(httpx.Client, "post", return_value=mock_response):
            with pytest.raises(WebhookNotFoundError, match="not found"):
                client.send_message(
                    webhook_url="https://prod-42.westus.logic.azure.com/workflows/invalid",
                    content="Hello!",
                )

    def test_send_message_rate_limit(self, client):
        """Test 429 error handling."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Too Many Requests"
        mock_response.headers = {"Retry-After": "60"}

        with patch.object(httpx.Client, "post", return_value=mock_response):
            with pytest.raises(WebhookRateLimitError) as exc_info:
                client.send_message(
                    webhook_url="https://prod-42.westus.logic.azure.com/workflows/test",
                    content="Hello!",
                )

        assert exc_info.value.retry_after == 60

    def test_send_message_bad_request(self, client):
        """Test 400 error handling."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        with patch.object(httpx.Client, "post", return_value=mock_response):
            with pytest.raises(WebhookError, match="Bad request"):
                client.send_message(
                    webhook_url="https://prod-42.westus.logic.azure.com/workflows/test",
                    content="",
                )

    def test_send_message_timeout(self, client):
        """Test timeout error handling."""
        with patch.object(
            httpx.Client,
            "post",
            side_effect=httpx.TimeoutException("Request timed out"),
        ):
            with pytest.raises(WebhookError, match="timed out"):
                client.send_message(
                    webhook_url="https://prod-42.westus.logic.azure.com/workflows/test",
                    content="Hello!",
                )


class TestTeamsWebhookClientSendAdaptiveCard:
    """Tests for send_adaptive_card method."""

    @pytest.fixture
    def client(self):
        return TeamsWebhookClient()

    def test_send_adaptive_card_success(self, client):
        """Test successful adaptive card sending."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "1"

        card_payload = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [{"type": "TextBlock", "text": "Hello"}],
        }

        with patch.object(httpx.Client, "post", return_value=mock_response) as mock_post:
            result = client.send_adaptive_card(
                webhook_url="https://prod-42.westus.logic.azure.com/workflows/test",
                card_payload=card_payload,
            )

        assert result["success"] is True
        # Verify card is wrapped correctly
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["type"] == "message"
        assert len(call_kwargs["json"]["attachments"]) == 1
        assert (
            call_kwargs["json"]["attachments"][0]["contentType"]
            == "application/vnd.microsoft.card.adaptive"
        )


class TestTeamsWebhookClientTestWebhook:
    """Tests for test_webhook method."""

    @pytest.fixture
    def client(self):
        return TeamsWebhookClient()

    def test_test_webhook_success(self, client):
        """Test successful webhook test."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "1"

        with patch.object(httpx.Client, "post", return_value=mock_response):
            result = client.test_webhook(
                webhook_url="https://prod-42.westus.logic.azure.com/workflows/test"
            )

        assert result["success"] is True
        assert result["error"] is None
        assert "latency_ms" in result

    def test_test_webhook_failure(self, client):
        """Test webhook test failure."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"

        with patch.object(httpx.Client, "post", return_value=mock_response):
            result = client.test_webhook(
                webhook_url="https://prod-42.westus.logic.azure.com/workflows/invalid"
            )

        assert result["success"] is False
        assert result["error"] is not None


class TestTeamsWebhookClientBuildAdaptiveCard:
    """Tests for build_adaptive_card static method."""

    def test_build_adaptive_card_basic(self):
        """Test building a basic adaptive card."""
        card = TeamsWebhookClient.build_adaptive_card(
            title="Test Title",
            body="Test Body",
        )

        assert card["type"] == "AdaptiveCard"
        assert card["version"] == "1.4"
        assert len(card["body"]) == 2
        assert card["body"][0]["text"] == "Test Title"
        assert card["body"][1]["text"] == "Test Body"
        assert "actions" not in card

    def test_build_adaptive_card_with_actions(self):
        """Test building adaptive card with actions."""
        card = TeamsWebhookClient.build_adaptive_card(
            title="Approval Request",
            body="Please approve this request.",
            actions=[
                {"label": "Approve", "url": "https://example.com/approve"},
                {"label": "Reject", "url": "https://example.com/reject"},
            ],
        )

        assert len(card["actions"]) == 2
        assert card["actions"][0]["title"] == "Approve"
        assert card["actions"][0]["url"] == "https://example.com/approve"
        assert card["actions"][1]["title"] == "Reject"

    def test_build_adaptive_card_skips_empty_actions(self):
        """Test that actions without URL are skipped."""
        card = TeamsWebhookClient.build_adaptive_card(
            title="Test",
            body="Body",
            actions=[
                {"label": "Valid", "url": "https://example.com"},
                {"label": "Invalid"},  # No URL
            ],
        )

        assert len(card["actions"]) == 1
        assert card["actions"][0]["title"] == "Valid"


_WORKFLOWS_URL = "https://prod-42.westus.logic.azure.com/workflows/test"


class TestAcceptedNon200Success:
    """A Teams "Workflows" (Power Automate) webhook answers 202, not 200.

    The suite only ever exercised 200 (the retired O365 connector's reply, body
    "1") and the error codes, so 202 fell through to the generic failure branch
    and every accepted message was reported as request_failed. These pin the
    whole 2xx range as success.
    """

    @pytest.fixture
    def client(self):
        return TeamsWebhookClient()

    @pytest.mark.parametrize("status", [200, 201, 202, 204])
    def test_any_2xx_is_success(self, client, status):
        mock_response = MagicMock()
        mock_response.status_code = status
        mock_response.text = ""  # Power Automate returns an EMPTY body on 202

        with patch.object(httpx.Client, "post", return_value=mock_response):
            result = client.send_message(webhook_url=_WORKFLOWS_URL, content="Hi")

        assert result["success"] is True
        assert result["response"] == ""

    def test_adaptive_card_accepted_202(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.text = ""

        with patch.object(httpx.Client, "post", return_value=mock_response):
            result = client.send_adaptive_card(
                webhook_url=_WORKFLOWS_URL, card_payload={"type": "AdaptiveCard"}
            )

        assert result["success"] is True

    def test_design_time_probe_accepts_202(self, client):
        # test_webhook() shares _send_to_webhook, so the same bug made the
        # builder's "Test connection" button fail against a live webhook.
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.text = ""

        with patch.object(httpx.Client, "post", return_value=mock_response):
            result = client.test_webhook(_WORKFLOWS_URL)

        assert result["success"] is True
        assert result["error"] is None

    @pytest.mark.parametrize("status", [301, 302, 500, 503])
    def test_non_2xx_still_fails(self, client, status):
        # Guard the other side of the boundary: widening to 2xx must not start
        # swallowing redirects or server errors.
        mock_response = MagicMock()
        mock_response.status_code = status
        mock_response.text = "nope"

        with patch.object(httpx.Client, "post", return_value=mock_response):
            with pytest.raises(WebhookError) as exc:
                client.send_message(webhook_url=_WORKFLOWS_URL, content="Hi")

        assert exc.value.status_code == status
