"""Tests for ExecuteTaskUseCase."""

from unittest.mock import patch

import pytest

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)

from app.layer2_application.services.email_client import (
    EmailAuthError,
    EmailError,
    EmailRateLimitError,
    EmailRecipientsRefusedError,
    GraphEmailClient,
    SmtpEmailClient,
)
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    EMAIL_INPUT_SCHEMA,
    EMAIL_OUTPUT_SCHEMA,
    _parse_recipients,
)


@pytest.fixture
def use_case(logger, monitor):
    """Create ExecuteTaskUseCase instance."""
    return ExecuteTaskUseCase(logger=logger, monitor=monitor)


@pytest.fixture
def smtp_cred_inputs():
    """Vault-resolved generic-basic-auth credential (JSON blob shape)."""
    return {
        "credential_ref": "c1",
        "__credentials": {
            "c1": {
                "type": "json",
                "config": {
                    "baseUrl": "https://smtp.example.com",
                    "username": "noreply@example.com",
                    "password": "secret",
                    "testPath": "/",
                },
            }
        },
    }


@pytest.fixture
def graph_cred_inputs():
    """Vault-resolved generic-oauth2-app credential (JSON blob shape)."""
    return {
        "credential_ref": "c1",
        "__credentials": {
            "c1": {
                "type": "json",
                "config": {
                    "tokenUrl": "https://login.example.com/tenant/oauth2/v2.0/token",
                    "clientId": "client-id",
                    "clientSecret": "client-secret",
                    "scopes": "",
                    "grantType": "client_credentials",
                    "testEndpoint": "https://graph.microsoft.com/v1.0/users",
                },
            }
        },
    }


def _command(inputs):
    return ExecuteTaskCommand(
        task_id="task-1",
        action="execute",
        parameters={},
        inputs=inputs,
    )


class TestInputOutputSchemas:
    """Tests for schema exports."""

    def test_input_schema_is_list(self):
        """Test that input schema is a list."""
        assert isinstance(EMAIL_INPUT_SCHEMA, list)
        assert len(EMAIL_INPUT_SCHEMA) > 0

    def test_output_schema_is_list(self):
        """Test that output schema is a list."""
        assert isinstance(EMAIL_OUTPUT_SCHEMA, list)
        assert len(EMAIL_OUTPUT_SCHEMA) > 0

    def test_input_schema_has_required_fields(self):
        """Test that input schema contains expected fields."""
        keys = [field["key"] for field in EMAIL_INPUT_SCHEMA]
        assert "operation_type" in keys
        assert "to_recipients" in keys
        assert "subject" in keys
        assert "body" in keys
        assert "sender" in keys
        assert "smtp_host" in keys

    def test_input_schema_has_no_secret_fields(self):
        """T5: no inline secret fields — auth comes only from the vault."""
        keys = [field["key"] for field in EMAIL_INPUT_SCHEMA]
        assert "password" not in keys
        assert "username" not in keys
        assert "client_secret" not in keys
        assert "client_id" not in keys


class TestParseRecipients:
    """Tests for the recipients parser."""

    def test_parse_comma_separated(self):
        assert _parse_recipients("a@x.com, b@x.com") == ["a@x.com", "b@x.com"]

    def test_parse_semicolon_separated(self):
        assert _parse_recipients("a@x.com; b@x.com") == ["a@x.com", "b@x.com"]

    def test_parse_list(self):
        assert _parse_recipients(["a@x.com", " b@x.com "]) == ["a@x.com", "b@x.com"]

    def test_parse_empty(self):
        assert _parse_recipients(None) == []
        assert _parse_recipients("") == []
        assert _parse_recipients(" , ") == []


class TestSendSmtp:
    """Tests for send_smtp operation."""

    def test_send_smtp_success(self, use_case, smtp_cred_inputs):
        """Test sending via SMTP with vault credentials."""
        inputs = {
            **smtp_cred_inputs,
            "operation_type": "send_smtp",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi there",
            "from_address": "noreply@example.com",
        }

        mock_response = {"success": True, "response": "<message-id@example.com>"}

        with patch.object(
            SmtpEmailClient, "send", return_value=mock_response
        ) as mock_send:
            result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["success"] is True
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["username"] == "noreply@example.com"
        assert call_kwargs["password"] == "secret"
        # host falls back to the credential's Base URL host
        assert call_kwargs["host"] == "smtp.example.com"
        assert call_kwargs["port"] == 587
        assert call_kwargs["security"] == "starttls"
        assert call_kwargs["to"] == ["alice@example.com"]

    def test_send_smtp_host_override_and_ssl_port_default(
        self, use_case, smtp_cred_inputs
    ):
        """Test explicit smtp_host and the SSL default port."""
        inputs = {
            **smtp_cred_inputs,
            "operation_type": "send_smtp",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi there",
            "from_address": "noreply@example.com",
            "smtp_host": "relay.other.example",
            "smtp_security": "ssl",
        }

        with patch.object(
            SmtpEmailClient, "send", return_value={"success": True, "response": "x"}
        ) as mock_send:
            result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["host"] == "relay.other.example"
        assert call_kwargs["port"] == 465
        assert call_kwargs["security"] == "ssl"

    def test_send_smtp_accepts_folded_basic_credential(self, use_case):
        """Test the control-plane's folded 'basic' credential shape."""
        inputs = {
            "credential_ref": "c1",
            "__credentials": {
                "c1": {
                    "type": "basic",
                    "config": {"username": "u", "password": "p"},
                }
            },
            "operation_type": "send_smtp",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi there",
            "from_address": "noreply@example.com",
            "smtp_host": "smtp.example.com",
        }

        with patch.object(
            SmtpEmailClient, "send", return_value={"success": True, "response": "x"}
        ):
            result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.SUCCESS

    def test_send_smtp_uses_smtp_basic_credential_server_fields(self, use_case):
        """smtp-basic credential supplies host/port/security — no node fields needed."""
        inputs = {
            "credential_ref": "c1",
            "__credentials": {
                "c1": {
                    "type": "json",
                    "config": {
                        "host": "smtp.office365.com",
                        "port": 465,
                        "security": "ssl",
                        "username": "noreply@company.com",
                        "password": "secret",
                    },
                }
            },
            "operation_type": "send_smtp",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi there",
            "from_address": "noreply@company.com",
        }

        with patch.object(
            SmtpEmailClient, "send", return_value={"success": True, "response": "x"}
        ) as mock_send:
            result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["host"] == "smtp.office365.com"
        assert call_kwargs["port"] == 465
        assert call_kwargs["security"] == "ssl"
        assert call_kwargs["username"] == "noreply@company.com"

    def test_node_fields_override_smtp_basic_credential(self, use_case):
        """A node smtp_host/security overrides the credential's server fields."""
        inputs = {
            "credential_ref": "c1",
            "__credentials": {
                "c1": {
                    "type": "json",
                    "config": {
                        "host": "smtp.office365.com",
                        "port": 465,
                        "security": "ssl",
                        "username": "u",
                        "password": "p",
                    },
                }
            },
            "operation_type": "send_smtp",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi there",
            "from_address": "noreply@company.com",
            "smtp_host": "relay.internal.example",
            "smtp_security": "starttls",
            "smtp_port": "2525",
        }

        with patch.object(
            SmtpEmailClient, "send", return_value={"success": True, "response": "x"}
        ) as mock_send:
            result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["host"] == "relay.internal.example"
        assert call_kwargs["security"] == "starttls"
        assert call_kwargs["port"] == 2525

    def test_send_smtp_missing_host_errors(self, use_case):
        """Test error when neither smtp_host nor credential Base URL is set."""
        inputs = {
            "credential_ref": "c1",
            "__credentials": {
                "c1": {"type": "basic", "config": {"username": "u", "password": "p"}}
            },
            "operation_type": "send_smtp",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi there",
            "from_address": "noreply@example.com",
        }

        result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"
        assert "smtp_host" in result.outputs["error"]

    def test_send_smtp_missing_from_address_errors(self, use_case, smtp_cred_inputs):
        """Test error when from_address is missing."""
        inputs = {
            **smtp_cred_inputs,
            "operation_type": "send_smtp",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi there",
        }

        result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"

    def test_send_smtp_invalid_port_errors(self, use_case, smtp_cred_inputs):
        """Test error when smtp_port is not a number."""
        inputs = {
            **smtp_cred_inputs,
            "operation_type": "send_smtp",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi there",
            "from_address": "noreply@example.com",
            "smtp_port": "not-a-port",
        }

        result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"

    def test_send_smtp_wrong_credential_type_errors(self, use_case):
        """Test error when the credential carries no username/password."""
        inputs = {
            "credential_ref": "c1",
            "__credentials": {
                "c1": {"type": "webhook_url", "config": {"url": "https://x"}}
            },
            "operation_type": "send_smtp",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi there",
            "from_address": "noreply@example.com",
            "smtp_host": "smtp.example.com",
        }

        result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"


class TestSendGraph:
    """Tests for send_graph operation."""

    def test_send_graph_success(self, use_case, graph_cred_inputs):
        """Test sending via Graph with a vault oauth2-app credential."""
        inputs = {
            **graph_cred_inputs,
            "operation_type": "send_graph",
            "to_recipients": "alice@example.com, bob@example.com",
            "subject": "Hello",
            "body": "<p>Hi</p>",
            "body_format": "html",
            "sender": "noreply@example.com",
        }

        with patch.object(
            GraphEmailClient, "fetch_token", return_value="access-token"
        ) as mock_token, patch.object(
            GraphEmailClient,
            "send_mail",
            return_value={"success": True, "response": "202 Accepted"},
        ) as mock_send:
            result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["success"] is True
        token_kwargs = mock_token.call_args[1]
        assert token_kwargs["client_id"] == "client-id"
        send_kwargs = mock_send.call_args[1]
        assert send_kwargs["access_token"] == "access-token"
        assert send_kwargs["sender"] == "noreply@example.com"
        assert send_kwargs["to"] == ["alice@example.com", "bob@example.com"]
        assert send_kwargs["html"] is True

    def test_send_graph_accepts_bearer_credential(self, use_case):
        """Test a pre-issued bearer token skips the token fetch."""
        inputs = {
            "credential_ref": "c1",
            "__credentials": {
                "c1": {"type": "bearer", "config": {"token": "pre-issued"}}
            },
            "operation_type": "send_graph",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi",
            "sender": "noreply@example.com",
        }

        with patch.object(GraphEmailClient, "fetch_token") as mock_token, patch.object(
            GraphEmailClient,
            "send_mail",
            return_value={"success": True, "response": "202 Accepted"},
        ) as mock_send:
            result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.SUCCESS
        mock_token.assert_not_called()
        assert mock_send.call_args[1]["access_token"] == "pre-issued"

    def test_send_graph_missing_sender_errors(self, use_case, graph_cred_inputs):
        """Test error when sender is missing."""
        inputs = {
            **graph_cred_inputs,
            "operation_type": "send_graph",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi",
        }

        result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"

    def test_send_graph_wrong_credential_type_errors(self, use_case, smtp_cred_inputs):
        """Test error when the credential is not usable for Graph."""
        inputs = {
            **smtp_cred_inputs,
            "operation_type": "send_graph",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi",
            "sender": "noreply@example.com",
        }

        result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"


class TestValidationAndErrors:
    """Cross-operation validation and error taxonomy tests."""

    def test_missing_credential_fails_closed(self, use_case):
        """Test that a missing credential is a validation error (fail closed)."""
        inputs = {
            "operation_type": "send_smtp",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi",
            "from_address": "noreply@example.com",
        }

        result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"
        assert "credential" in result.outputs["error"]

    def test_missing_operation_type_errors(self, use_case, smtp_cred_inputs):
        """Test error when operation_type is missing."""
        inputs = {
            **smtp_cred_inputs,
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi",
        }

        result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"

    @pytest.mark.parametrize(
        "missing_field", ["to_recipients", "subject", "body"]
    )
    def test_missing_required_field_errors(
        self, use_case, smtp_cred_inputs, missing_field
    ):
        """Test error for each missing required message field."""
        inputs = {
            **smtp_cred_inputs,
            "operation_type": "send_smtp",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi",
            "from_address": "noreply@example.com",
        }
        inputs.pop(missing_field)

        result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"

    def test_invalid_body_format_errors(self, use_case, smtp_cred_inputs):
        """Test error for an unknown body_format."""
        inputs = {
            **smtp_cred_inputs,
            "operation_type": "send_smtp",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi",
            "from_address": "noreply@example.com",
            "body_format": "richtext",
        }

        result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"

    @pytest.mark.parametrize(
        ("raised", "expected_code"),
        [
            (EmailAuthError("bad login", error_code="auth_failed"), "AUTH_ERROR"),
            (
                EmailRecipientsRefusedError("refused", error_code="recipients_refused"),
                "RECIPIENTS_REFUSED",
            ),
            (
                EmailRateLimitError("slow down", error_code="rate_limit"),
                "RATE_LIMIT_EXCEEDED",
            ),
            (EmailError("timed out", error_code="timeout"), "timeout"),
            (EmailError("boom"), "EMAIL_ERROR"),
            (RuntimeError("surprise"), "UNKNOWN_ERROR"),
        ],
    )
    def test_error_taxonomy(self, use_case, smtp_cred_inputs, raised, expected_code):
        """Test typed client errors map to the documented error codes."""
        inputs = {
            **smtp_cred_inputs,
            "operation_type": "send_smtp",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi",
            "from_address": "noreply@example.com",
        }

        with patch.object(SmtpEmailClient, "send", side_effect=raised):
            result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["success"] is False
        assert result.outputs["error_code"] == expected_code

    def test_metrics_tracked(self, use_case, monitor, smtp_cred_inputs):
        """Test request/success metrics are emitted."""
        inputs = {
            **smtp_cred_inputs,
            "operation_type": "send_smtp",
            "to_recipients": "alice@example.com",
            "subject": "Hello",
            "body": "Hi",
            "from_address": "noreply@example.com",
        }

        with patch.object(
            SmtpEmailClient, "send", return_value={"success": True, "response": "x"}
        ):
            use_case.execute(_command(inputs))

        metric_names = [name for name, _ in monitor.tracked]
        assert "email_task_requests" in metric_names
        assert "email_task_success" in metric_names
