"""Tests for ExecuteTaskUseCase."""

from unittest.mock import patch

import pytest

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)

from app.layer2_application.services.webhook_client import (
    TeamsWebhookClient,
    WebhookError,
    WebhookNotFoundError,
    WebhookRateLimitError,
)
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    MS_TEAMS_INPUT_SCHEMA,
    MS_TEAMS_OUTPUT_SCHEMA,
)


@pytest.fixture
def use_case(logger, monitor):
    """Create ExecuteTaskUseCase instance."""
    return ExecuteTaskUseCase(logger=logger, monitor=monitor)


@pytest.fixture
def base_inputs():
    """Base inputs for all tests — webhook URL borrowed from the vault (T5: no inline)."""
    return {
        "credential_ref": "c1",
        "__credentials": {"c1": {"type": "webhook_url", "config": {
            "url": "https://prod-42.westus.logic.azure.com/workflows/test-webhook"}}},
    }


class TestInputOutputSchemas:
    """Tests for schema exports."""

    def test_input_schema_is_list(self):
        """Test that input schema is a list."""
        assert isinstance(MS_TEAMS_INPUT_SCHEMA, list)
        assert len(MS_TEAMS_INPUT_SCHEMA) > 0

    def test_output_schema_is_list(self):
        """Test that output schema is a list."""
        assert isinstance(MS_TEAMS_OUTPUT_SCHEMA, list)
        assert len(MS_TEAMS_OUTPUT_SCHEMA) > 0

    def test_input_schema_has_required_fields(self):
        """Test that input schema contains expected fields."""
        keys = [field["key"] for field in MS_TEAMS_INPUT_SCHEMA]
        assert "webhook_url" not in keys  # T5 hard-remove: inline secret field is gone
        assert "operation_type" in keys
        assert "message_content" in keys
        assert "card_title" in keys
        assert "card_body" in keys


class TestSendMessage:
    """Tests for send_message operation."""

    def test_send_message_success(self, use_case, base_inputs):
        """Test sending a message."""
        inputs = {
            **base_inputs,
            "operation_type": "send_message",
            "message_content": "Hello Teams!",
        }

        mock_response = {"success": True, "response": "1"}

        with patch.object(
            TeamsWebhookClient, "send_message", return_value=mock_response
        ):
            command = ExecuteTaskCommand(
                task_id="task-1",
                action="execute",
                parameters={},
                inputs=inputs,
            )
            result = use_case.execute(command)

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["success"] is True

    def test_send_message_with_title(self, use_case, base_inputs):
        """Test sending a message with title."""
        inputs = {
            **base_inputs,
            "operation_type": "send_message",
            "message_title": "Important",
            "message_content": "Hello Teams!",
        }

        mock_response = {"success": True, "response": "1"}

        with patch.object(
            TeamsWebhookClient, "send_message", return_value=mock_response
        ) as mock_send:
            command = ExecuteTaskCommand(
                task_id="task-2",
                action="execute",
                parameters={},
                inputs=inputs,
            )
            result = use_case.execute(command)

        assert result.status == TaskStatus.SUCCESS
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["title"] == "Important"

    def test_send_message_missing_content(self, use_case, base_inputs):
        """Test error when message_content is missing."""
        inputs = {
            **base_inputs,
            "operation_type": "send_message",
            "message_content": "",  # Empty
        }

        command = ExecuteTaskCommand(
            task_id="task-3",
            action="execute",
            parameters={},
            inputs=inputs,
        )
        result = use_case.execute(command)

        assert result.status == TaskStatus.ERROR
        assert "message_content" in result.outputs["error"]


class TestSendAdaptiveCard:
    """Tests for send_adaptive_card operation."""

    def test_send_adaptive_card_success(self, use_case, base_inputs):
        """Test sending an Adaptive Card."""
        inputs = {
            **base_inputs,
            "operation_type": "send_adaptive_card",
            "card_title": "Approval Required",
            "card_body": "Please review and approve",
            "card_actions": [
                {"label": "Approve", "url": "https://example.com/approve"},
            ],
        }

        mock_response = {"success": True, "response": "1"}

        with patch.object(
            TeamsWebhookClient, "send_adaptive_card", return_value=mock_response
        ):
            command = ExecuteTaskCommand(
                task_id="task-4",
                action="execute",
                parameters={},
                inputs=inputs,
            )
            result = use_case.execute(command)

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["success"] is True

    def test_send_adaptive_card_missing_title(self, use_case, base_inputs):
        """Test error when card_title is missing."""
        inputs = {
            **base_inputs,
            "operation_type": "send_adaptive_card",
            "card_title": "",
            "card_body": "Body text",
        }

        command = ExecuteTaskCommand(
            task_id="task-5",
            action="execute",
            parameters={},
            inputs=inputs,
        )
        result = use_case.execute(command)

        assert result.status == TaskStatus.ERROR
        assert "card_title" in result.outputs["error"]

    def test_send_adaptive_card_missing_body(self, use_case, base_inputs):
        """Test error when card_body is missing."""
        inputs = {
            **base_inputs,
            "operation_type": "send_adaptive_card",
            "card_title": "Title",
            "card_body": "",
        }

        command = ExecuteTaskCommand(
            task_id="task-6",
            action="execute",
            parameters={},
            inputs=inputs,
        )
        result = use_case.execute(command)

        assert result.status == TaskStatus.ERROR
        assert "card_body" in result.outputs["error"]

    def test_send_adaptive_card_json_string_actions(self, use_case, base_inputs):
        """Test that JSON string actions are parsed."""
        inputs = {
            **base_inputs,
            "operation_type": "send_adaptive_card",
            "card_title": "Test",
            "card_body": "Test body",
            "card_actions": '[{"label": "Click", "url": "https://example.com"}]',
        }

        mock_response = {"success": True, "response": "1"}

        with patch.object(
            TeamsWebhookClient, "send_adaptive_card", return_value=mock_response
        ):
            command = ExecuteTaskCommand(
                task_id="task-7",
                action="execute",
                parameters={},
                inputs=inputs,
            )
            result = use_case.execute(command)

        assert result.status == TaskStatus.SUCCESS


class TestValidation:
    """Tests for input validation."""

    def test_missing_webhook_url(self, use_case):
        """Test error when webhook_url is missing."""
        inputs = {
            "operation_type": "send_message",
            "message_content": "Hello",
        }

        command = ExecuteTaskCommand(
            task_id="task-10",
            action="execute",
            parameters={},
            inputs=inputs,
        )
        result = use_case.execute(command)

        assert result.status == TaskStatus.ERROR
        assert "webhook_url" in result.outputs["error"]

    def test_invalid_webhook_url(self, use_case):
        """Test error when the vault-supplied webhook_url is not HTTPS."""
        inputs = {
            "credential_ref": "c1",
            "__credentials": {"c1": {"type": "webhook_url", "config": {
                "url": "http://not-secure.com/webhook"}}},
            "operation_type": "send_message",
            "message_content": "Hello",
        }

        command = ExecuteTaskCommand(
            task_id="task-11",
            action="execute",
            parameters={},
            inputs=inputs,
        )
        result = use_case.execute(command)

        assert result.status == TaskStatus.ERROR
        assert "HTTPS" in result.outputs["error"]

    def test_missing_operation_type(self, use_case, base_inputs):
        """Test error when operation_type is missing."""
        inputs = {
            **base_inputs,
            "message_content": "Hello",
        }

        command = ExecuteTaskCommand(
            task_id="task-12",
            action="execute",
            parameters={},
            inputs=inputs,
        )
        result = use_case.execute(command)

        assert result.status == TaskStatus.ERROR
        assert "operation_type" in result.outputs["error"]

    def test_invalid_operation_type(self, use_case, base_inputs):
        """Test error when operation_type is invalid."""
        inputs = {
            **base_inputs,
            "operation_type": "invalid_operation",
            "message_content": "Hello",
        }

        command = ExecuteTaskCommand(
            task_id="task-13",
            action="execute",
            parameters={},
            inputs=inputs,
        )
        result = use_case.execute(command)

        assert result.status == TaskStatus.ERROR
        assert "operation type" in result.outputs["error"].lower()


class TestErrorHandling:
    """Tests for error handling."""

    def test_webhook_not_found_error(self, use_case, base_inputs):
        """Test handling of webhook not found error."""
        inputs = {
            **base_inputs,
            "operation_type": "send_message",
            "message_content": "Hello",
        }

        with patch.object(
            TeamsWebhookClient,
            "send_message",
            side_effect=WebhookNotFoundError("Webhook not found"),
        ):
            command = ExecuteTaskCommand(
                task_id="task-20",
                action="execute",
                parameters={},
                inputs=inputs,
            )
            result = use_case.execute(command)

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "WEBHOOK_NOT_FOUND"

    def test_rate_limit_error(self, use_case, base_inputs):
        """Test handling of rate limit error."""
        inputs = {
            **base_inputs,
            "operation_type": "send_message",
            "message_content": "Hello",
        }

        with patch.object(
            TeamsWebhookClient,
            "send_message",
            side_effect=WebhookRateLimitError("Rate limit exceeded", retry_after=60),
        ):
            command = ExecuteTaskCommand(
                task_id="task-21",
                action="execute",
                parameters={},
                inputs=inputs,
            )
            result = use_case.execute(command)

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "RATE_LIMIT_EXCEEDED"

    def test_generic_webhook_error(self, use_case, base_inputs):
        """Test handling of generic webhook error."""
        inputs = {
            **base_inputs,
            "operation_type": "send_message",
            "message_content": "Hello",
        }

        with patch.object(
            TeamsWebhookClient,
            "send_message",
            side_effect=WebhookError("Something went wrong", error_code="bad_request"),
        ):
            command = ExecuteTaskCommand(
                task_id="task-22",
                action="execute",
                parameters={},
                inputs=inputs,
            )
            result = use_case.execute(command)

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "bad_request"

    def test_unexpected_error(self, use_case, base_inputs):
        """Test handling of unexpected error."""
        inputs = {
            **base_inputs,
            "operation_type": "send_message",
            "message_content": "Hello",
        }

        with patch.object(
            TeamsWebhookClient,
            "send_message",
            side_effect=Exception("Unexpected error"),
        ):
            command = ExecuteTaskCommand(
                task_id="task-23",
                action="execute",
                parameters={},
                inputs=inputs,
            )
            result = use_case.execute(command)

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "UNKNOWN_ERROR"
