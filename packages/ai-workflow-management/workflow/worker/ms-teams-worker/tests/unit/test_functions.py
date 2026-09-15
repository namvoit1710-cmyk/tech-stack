"""Tests for design-time functions."""

from unittest.mock import patch

import pytest

from app.functions import (
    MS_TEAMS_WORKER_FUNCTIONS,
    test_webhook as verify_webhook,  # Alias to avoid pytest collection
)
from app.layer2_application.services.webhook_client import (
    TeamsWebhookClient,
    WebhookError,
)


class TestWorkerFunctionsExport:
    """Tests for worker functions export."""

    def test_functions_list_not_empty(self):
        """Test that functions list is not empty."""
        assert len(MS_TEAMS_WORKER_FUNCTIONS) > 0

    def test_functions_have_required_attributes(self):
        """Test that all functions have required attributes."""
        for func in MS_TEAMS_WORKER_FUNCTIONS:
            assert func.name
            assert func.description
            assert isinstance(func.input_schema, list)
            assert isinstance(func.output_schema, list)
            assert func.handler is not None

    def test_function_names(self):
        """Test expected function names."""
        names = [f.name for f in MS_TEAMS_WORKER_FUNCTIONS]
        assert "test_webhook" in names


_URL = "https://prod-42.westus.logic.azure.com/workflows/test"


def _params(url: str = _URL, cred_type: str = "webhook_url") -> dict:
    """The shape the control plane hands a design-time function.

    Flat `credential_ref` + the resolved `__credentials` map — exactly what
    `_resolve_credentials_into_params` produces. The URL is NEVER an inline
    param: T5 removed that field, and the point of the fix is that the test
    button proves the STORED credential.
    """
    return {
        "credential_ref": "c1",
        "__credentials": {"c1": {"type": cred_type, "config": {"url": url}}},
    }


class TestTestWebhookFunction:
    """Tests for test_webhook function."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Test successful webhook test."""
        mock_result = {
            "success": True,
            "latency_ms": 150.5,
            "error": None,
        }

        with patch.object(
            TeamsWebhookClient, "test_webhook", return_value=mock_result
        ) as probe:
            result = await verify_webhook(_params())

        assert result["success"] is True
        assert result["message"] == "Webhook connection test successful"
        assert result["latency_ms"] == 150.5
        assert result["error"] is None
        # The URL probed is the one from the vault credential, not a form field.
        assert probe.call_args.args[0] == _URL

    @pytest.mark.asyncio
    async def test_no_credential_fails_closed(self):
        """No credential selected -> fail closed, never probe."""
        with patch.object(TeamsWebhookClient, "test_webhook") as probe:
            result = await verify_webhook({})

        assert result["success"] is False
        assert "credential" in result["error"].lower()
        probe.assert_not_called()

    @pytest.mark.asyncio
    async def test_inline_webhook_url_is_ignored(self):
        """REGRESSION (T5): an inline param must NOT authenticate the probe.

        The old implementation read `params["webhook_url"]`, so the button
        tested whatever was pasted into the form rather than the credential the
        node actually runs with — a green test proved nothing.
        """
        with patch.object(TeamsWebhookClient, "test_webhook") as probe:
            result = await verify_webhook({"webhook_url": _URL})

        assert result["success"] is False
        probe.assert_not_called()

    @pytest.mark.asyncio
    async def test_wrong_credential_kind(self):
        """A non-webhook_url credential yields no URL."""
        result = await verify_webhook(_params(cred_type="bearer"))

        assert result["success"] is False
        assert "webhook_url" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_webhook_url(self):
        """Test error when the credential's URL is blank."""
        result = await verify_webhook(_params(url="   "))

        assert result["success"] is False
        assert "webhook_url" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_webhook_url(self):
        """Test error when the credential's URL is not HTTPS."""
        result = await verify_webhook(_params(url="http://not-secure.com/webhook"))

        assert result["success"] is False
        assert "HTTPS" in result["error"]

    @pytest.mark.asyncio
    async def test_webhook_failure(self):
        """Test handling of webhook test failure."""
        mock_result = {
            "success": False,
            "latency_ms": 100.0,
            "error": "Webhook not found",
        }

        with patch.object(
            TeamsWebhookClient, "test_webhook", return_value=mock_result
        ):
            result = await verify_webhook(_params())

        assert result["success"] is False
        assert result["message"] is None
        assert result["error"] == "Webhook not found"

    @pytest.mark.asyncio
    async def test_webhook_error_exception(self):
        """Test handling of WebhookError exception."""
        with patch.object(
            TeamsWebhookClient,
            "test_webhook",
            side_effect=WebhookError("Connection failed"),
        ):
            result = await verify_webhook(_params())

        assert result["success"] is False
        assert "Connection failed" in result["error"]

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        """Test handling of unexpected exception."""
        with patch.object(
            TeamsWebhookClient,
            "test_webhook",
            side_effect=Exception("Unexpected error"),
        ):
            result = await verify_webhook(_params())

        assert result["success"] is False
        assert "Connection test failed" in result["error"]
        assert "Unexpected error" in result["error"]

    def test_declares_no_inline_secret_field(self):
        """T5 guard: the function must not ask the user for the URL."""
        fn = next(f for f in MS_TEAMS_WORKER_FUNCTIONS if f.name == "test_webhook")
        assert [f["key"] for f in fn.input_schema] == []
