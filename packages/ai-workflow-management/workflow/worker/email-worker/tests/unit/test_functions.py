"""Tests for Email worker dynamic functions."""

from unittest.mock import patch

from app.functions import EMAIL_WORKER_FUNCTIONS
from app.functions import test_graph_credential as fn_test_graph_credential
from app.functions import test_smtp_connection as fn_test_smtp_connection
from app.layer2_application.services.email_client import (
    EmailAuthError,
    GraphEmailClient,
    SmtpEmailClient,
)


class TestFunctionDefinitions:
    """Tests for the exported WorkerFunction list."""

    def test_functions_are_registered(self):
        names = [f.name for f in EMAIL_WORKER_FUNCTIONS]
        assert names == ["test_smtp_connection", "test_graph_credential"]


class TestTestSmtpConnection:
    """Tests for the test_smtp_connection function."""

    async def test_missing_host(self):
        result = await fn_test_smtp_connection({})
        assert result["success"] is False
        assert "host" in result["error"]

    async def test_invalid_security(self):
        result = await fn_test_smtp_connection(
            {"host": "smtp.example.com", "security": "tls13"}
        )
        assert result["success"] is False
        assert "security" in result["error"]

    async def test_invalid_port(self):
        result = await fn_test_smtp_connection(
            {"host": "smtp.example.com", "port": "abc"}
        )
        assert result["success"] is False
        assert "port" in result["error"]

    async def test_success(self):
        with patch.object(
            SmtpEmailClient,
            "test_connection",
            return_value={"success": True, "latency_ms": 12.3, "error": None},
        ) as mock_test:
            result = await fn_test_smtp_connection(
                {"host": "smtp.example.com", "username": "u", "password": "p"}
            )

        assert result["success"] is True
        assert result["latency_ms"] == 12.3
        call_kwargs = mock_test.call_args[1]
        assert call_kwargs["port"] == 587  # starttls default

    async def test_failure_reported(self):
        with patch.object(
            SmtpEmailClient,
            "test_connection",
            return_value={"success": False, "latency_ms": 5.0, "error": "bad login"},
        ):
            result = await fn_test_smtp_connection({"host": "smtp.example.com"})

        assert result["success"] is False
        assert result["error"] == "bad login"


class TestTestGraphCredential:
    """Tests for the test_graph_credential function."""

    async def test_missing_token_url(self):
        result = await fn_test_graph_credential({"client_id": "c", "client_secret": "s"})
        assert result["success"] is False
        assert "token_url" in result["error"]

    async def test_non_https_token_url(self):
        result = await fn_test_graph_credential(
            {"token_url": "http://x", "client_id": "c", "client_secret": "s"}
        )
        assert result["success"] is False

    async def test_missing_client_id(self):
        result = await fn_test_graph_credential(
            {"token_url": "https://login.test/token", "client_secret": "s"}
        )
        assert result["success"] is False
        assert "client_id" in result["error"]

    async def test_success(self):
        with patch.object(GraphEmailClient, "fetch_token", return_value="tok"):
            result = await fn_test_graph_credential(
                {
                    "token_url": "https://login.test/token",
                    "client_id": "c",
                    "client_secret": "s",
                }
            )

        assert result["success"] is True
        assert result["latency_ms"] is not None

    async def test_auth_failure_reported(self):
        with patch.object(
            GraphEmailClient,
            "fetch_token",
            side_effect=EmailAuthError("rejected", error_code="auth_failed"),
        ):
            result = await fn_test_graph_credential(
                {
                    "token_url": "https://login.test/token",
                    "client_id": "c",
                    "client_secret": "s",
                }
            )

        assert result["success"] is False
        assert "rejected" in result["error"]
