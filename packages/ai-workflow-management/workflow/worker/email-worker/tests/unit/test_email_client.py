"""Tests for the SMTP and Graph e-mail clients."""

import smtplib
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.layer2_application.services.email_client import (
    EmailAuthError,
    EmailError,
    EmailRateLimitError,
    EmailRecipientsRefusedError,
    GraphEmailClient,
    SmtpEmailClient,
)


_SEND_ARGS = dict(
    host="smtp.example.com",
    port=587,
    security="starttls",
    username="u",
    password="p",
    from_address="noreply@example.com",
    to=["alice@example.com"],
    cc=["carol@example.com"],
    bcc=["bob@example.com"],
    subject="Hello",
    body="Hi there",
)


class TestSmtpEmailClient:
    """Tests for SmtpEmailClient (smtplib mocked)."""

    @patch("app.layer2_application.services.email_client.smtplib.SMTP")
    def test_send_starttls_success(self, mock_smtp_cls):
        """Test the STARTTLS happy path: starttls, login, send."""
        smtp = mock_smtp_cls.return_value
        smtp.__enter__.return_value = smtp
        smtp.send_message.return_value = {}

        result = SmtpEmailClient().send(**_SEND_ARGS)

        assert result["success"] is True
        assert result["response"]  # generated Message-ID
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("u", "p")
        send_kwargs = smtp.send_message.call_args[1]
        assert send_kwargs["from_addr"] == "noreply@example.com"
        assert send_kwargs["to_addrs"] == [
            "alice@example.com",
            "carol@example.com",
            "bob@example.com",
        ]

    @patch("app.layer2_application.services.email_client.smtplib.SMTP_SSL")
    def test_send_ssl_uses_smtp_ssl(self, mock_smtp_ssl_cls):
        """Test that security=ssl uses SMTP_SSL and skips starttls."""
        smtp = mock_smtp_ssl_cls.return_value
        smtp.__enter__.return_value = smtp
        smtp.send_message.return_value = {}

        result = SmtpEmailClient().send(**{**_SEND_ARGS, "security": "ssl", "port": 465})

        assert result["success"] is True
        smtp.starttls.assert_not_called()

    @patch("app.layer2_application.services.email_client.smtplib.SMTP")
    def test_send_without_username_skips_login(self, mock_smtp_cls):
        """Test that an empty username skips login (open relay)."""
        smtp = mock_smtp_cls.return_value
        smtp.__enter__.return_value = smtp
        smtp.send_message.return_value = {}

        SmtpEmailClient().send(**{**_SEND_ARGS, "username": "", "password": ""})

        smtp.login.assert_not_called()

    @patch("app.layer2_application.services.email_client.smtplib.SMTP")
    def test_send_html_builds_alternative(self, mock_smtp_cls):
        """Test that html=True produces a text/html alternative part."""
        smtp = mock_smtp_cls.return_value
        smtp.__enter__.return_value = smtp
        smtp.send_message.return_value = {}

        SmtpEmailClient().send(**{**_SEND_ARGS, "body": "<p>Hi</p>", "html": True})

        message = smtp.send_message.call_args[0][0]
        assert message.get_body(("html",)) is not None

    @patch("app.layer2_application.services.email_client.smtplib.SMTP")
    def test_auth_failure_raises_typed_error(self, mock_smtp_cls):
        """Test SMTPAuthenticationError maps to EmailAuthError."""
        smtp = mock_smtp_cls.return_value
        smtp.__enter__.return_value = smtp
        smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")

        with pytest.raises(EmailAuthError) as exc:
            SmtpEmailClient().send(**_SEND_ARGS)
        assert exc.value.error_code == "auth_failed"

    @patch("app.layer2_application.services.email_client.smtplib.SMTP")
    def test_partial_refusal_raises_typed_error(self, mock_smtp_cls):
        """Test a non-empty refused dict maps to EmailRecipientsRefusedError."""
        smtp = mock_smtp_cls.return_value
        smtp.__enter__.return_value = smtp
        smtp.send_message.return_value = {"bob@example.com": (550, b"mailbox full")}

        with pytest.raises(EmailRecipientsRefusedError):
            SmtpEmailClient().send(**_SEND_ARGS)

    @patch("app.layer2_application.services.email_client.smtplib.SMTP")
    def test_all_recipients_refused_raises_typed_error(self, mock_smtp_cls):
        """Test SMTPRecipientsRefused maps to EmailRecipientsRefusedError."""
        smtp = mock_smtp_cls.return_value
        smtp.__enter__.return_value = smtp
        smtp.send_message.side_effect = smtplib.SMTPRecipientsRefused(
            {"alice@example.com": (550, b"no such user")}
        )

        with pytest.raises(EmailRecipientsRefusedError):
            SmtpEmailClient().send(**_SEND_ARGS)

    @patch("app.layer2_application.services.email_client.smtplib.SMTP")
    def test_connection_failure_raises_email_error(self, mock_smtp_cls):
        """Test OSError (connect/timeout) maps to EmailError."""
        mock_smtp_cls.side_effect = OSError("connection refused")

        with pytest.raises(EmailError) as exc:
            SmtpEmailClient().send(**_SEND_ARGS)
        assert exc.value.error_code == "connection_failed"

    @patch("app.layer2_application.services.email_client.smtplib.SMTP")
    def test_test_connection_success(self, mock_smtp_cls):
        """Test test_connection returns latency on success."""
        smtp = mock_smtp_cls.return_value
        smtp.__enter__.return_value = smtp

        result = SmtpEmailClient().test_connection(
            host="smtp.example.com",
            port=587,
            security="starttls",
            username="u",
            password="p",
        )

        assert result["success"] is True
        assert result["latency_ms"] >= 0

    @patch("app.layer2_application.services.email_client.smtplib.SMTP")
    def test_test_connection_failure_is_reported_not_raised(self, mock_smtp_cls):
        """Test test_connection reports failures instead of raising."""
        mock_smtp_cls.side_effect = OSError("connection refused")

        result = SmtpEmailClient().test_connection(
            host="smtp.example.com",
            port=587,
            security="starttls",
            username="",
            password="",
        )

        assert result["success"] is False
        assert "refused" in result["error"]


def _graph_client(handler):
    """Build a GraphEmailClient backed by an httpx MockTransport."""
    return GraphEmailClient(
        graph_base_url="https://graph.test/v1.0",
        transport=httpx.MockTransport(handler),
    )


_TOKEN_ARGS = dict(
    token_url="https://login.test/token",
    client_id="cid",
    client_secret="cs",
)


class TestGraphEmailClientToken:
    """Tests for the OAuth2 client-credentials token fetch."""

    def test_fetch_token_success_and_default_scope(self):
        """Test the token fetch happy path with the Graph default scope."""
        seen = {}

        def handler(request):
            seen["content"] = request.content.decode()
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})

        token = _graph_client(handler).fetch_token(**_TOKEN_ARGS)

        assert token == "tok"
        assert "graph.microsoft.com%2F.default" in seen["content"]

    def test_fetch_token_rejection_raises_auth_error_without_body_echo(self):
        """Test 401 maps to EmailAuthError and the body is not echoed."""

        def handler(request):
            return httpx.Response(401, json={"error": "invalid_client secret-detail"})

        with pytest.raises(EmailAuthError) as exc:
            _graph_client(handler).fetch_token(**_TOKEN_ARGS)
        assert "secret-detail" not in str(exc.value)

    def test_fetch_token_server_error_raises_email_error(self):
        """Test 500 maps to EmailError with token_error code."""

        def handler(request):
            return httpx.Response(500, text="oops")

        with pytest.raises(EmailError) as exc:
            _graph_client(handler).fetch_token(**_TOKEN_ARGS)
        assert exc.value.error_code == "token_error"

    def test_fetch_token_missing_access_token_raises(self):
        """Test a 200 without access_token raises EmailError."""

        def handler(request):
            return httpx.Response(200, json={"token_type": "Bearer"})

        with pytest.raises(EmailError, match="missing access_token"):
            _graph_client(handler).fetch_token(**_TOKEN_ARGS)


_MAIL_ARGS = dict(
    access_token="tok",
    sender="noreply@example.com",
    to=["alice@example.com"],
    cc=[],
    bcc=["bob@example.com"],
    subject="Hello",
    body="Hi",
)


class TestGraphEmailClientSendMail:
    """Tests for the Graph sendMail call."""

    def test_send_mail_success(self):
        """Test 202 Accepted with the expected payload and auth header."""
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            import json

            seen["payload"] = json.loads(request.content)
            return httpx.Response(202)

        result = _graph_client(handler).send_mail(**_MAIL_ARGS)

        assert result == {"success": True, "response": "202 Accepted"}
        assert seen["url"].endswith("/users/noreply@example.com/sendMail")
        assert seen["auth"] == "Bearer tok"
        message = seen["payload"]["message"]
        assert message["subject"] == "Hello"
        assert message["body"]["contentType"] == "Text"
        assert message["toRecipients"] == [
            {"emailAddress": {"address": "alice@example.com"}}
        ]
        assert message["bccRecipients"] == [
            {"emailAddress": {"address": "bob@example.com"}}
        ]
        assert "ccRecipients" not in message

    def test_send_mail_html_content_type(self):
        """Test html=True sets the HTML content type."""

        def handler(request):
            import json

            payload = json.loads(request.content)
            assert payload["message"]["body"]["contentType"] == "HTML"
            return httpx.Response(202)

        _graph_client(handler).send_mail(**{**_MAIL_ARGS, "html": True})

    def test_send_mail_auth_rejection(self):
        """Test 403 maps to EmailAuthError."""

        def handler(request):
            return httpx.Response(403)

        with pytest.raises(EmailAuthError):
            _graph_client(handler).send_mail(**_MAIL_ARGS)

    def test_send_mail_sender_not_found(self):
        """Test 404 maps to EmailError with sender_not_found."""

        def handler(request):
            return httpx.Response(404)

        with pytest.raises(EmailError) as exc:
            _graph_client(handler).send_mail(**_MAIL_ARGS)
        assert exc.value.error_code == "sender_not_found"

    def test_send_mail_rate_limit_with_retry_after(self):
        """Test 429 maps to EmailRateLimitError carrying Retry-After."""

        def handler(request):
            return httpx.Response(429, headers={"Retry-After": "17"})

        with pytest.raises(EmailRateLimitError) as exc:
            _graph_client(handler).send_mail(**_MAIL_ARGS)
        assert exc.value.retry_after == 17

    def test_send_mail_other_error(self):
        """Test an unexpected status maps to EmailError with graph_error."""

        def handler(request):
            return httpx.Response(500, text="boom")

        with pytest.raises(EmailError) as exc:
            _graph_client(handler).send_mail(**_MAIL_ARGS)
        assert exc.value.error_code == "graph_error"
