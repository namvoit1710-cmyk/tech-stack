"""Gap-fill unit tests for MS Teams Worker (unit-test-coverage sweep 2026-07-02).

Targets the branches left uncovered by the pre-existing suite (baseline 98%):
  - execute_task_usecase.py:280   defensive "Unsupported operation type" fallthrough
  - execute_task_usecase.py:364-365 malformed JSON string in card_actions
  - webhook_client.py:202          non-mapped non-2xx status (e.g. 500)
  - webhook_client.py:214          generic httpx.HTTPError (non-timeout transport error)

Idiom follows the worker-sdk base contract + sibling test_execute_task.py /
test_webhook_client.py: sync DummyLogger/DummyMonitor from conftest, use-case is
SYNC so MagicMock (never AsyncMock), HTTP mocked at the httpx.Client.post seam,
duration assertions use >= 0 (Windows monotonic can read 0 on fast paths).
Every expected value is grounded in the source, not convention.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)

from app.layer1_domain.value_objects.operation_type import OperationType
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
)
from app.layer2_application.services.webhook_client import (
    TeamsWebhookClient,
    WebhookError,
    WebhookNotFoundError,
)

# A LIVE Teams "Workflows" (Power Automate) webhook shape.
#
# These tests were written before the O365-retirement guard
# (webhook_client.validate_webhook_url) landed, using the then-normal
# `outlook.office.com/webhook/...` shape. The guard now rejects that shape before
# any request is attempted, so every test below short-circuited into
# WebhookRetiredError and asserted against it instead of the branch it names —
# they had been covering NOTHING of their stated targets. The URL must stay
# non-retired for the 500 / ConnectError / fallthrough paths to be reachable at
# all. Retirement itself is covered in test_sa1548_hardening.py.
WORKFLOWS_URL = "https://prod-42.westus.logic.azure.com/workflows/gapfill/triggers/manual/paths/invoke"


@pytest.fixture
def use_case(logger, monitor):
    """Create ExecuteTaskUseCase instance (sync collaborators -> real dummies)."""
    return ExecuteTaskUseCase(logger=logger, monitor=monitor)


@pytest.fixture
def base_inputs():
    """Vault-supplied HTTPS webhook_url credential (T5: no inline field)."""
    return {
        "credential_ref": "c1",
        "__credentials": {
            "c1": {
                "type": "webhook_url",
                "config": {"url": WORKFLOWS_URL},
            }
        },
    }


def _command(inputs, task_id="gap-task"):
    return ExecuteTaskCommand(
        task_id=task_id, action="execute", parameters={}, inputs=inputs
    )


# -- execute_task_usecase.py:280 — defensive unsupported-operation fallthrough --
# FAILURE PATH. This branch is unreachable via normal input (_get_operation_type
# raises on anything not in the 2-member enum before this line is hit), so it is
# defensive code. We exercise it honestly by patching the resolver to return an
# OperationType value that neither elif matches — the only route to line 280.
class TestUnsupportedOperationFallthrough:
    def test_operation_type_not_dispatched_raises_validation_error(
        self, use_case, base_inputs
    ):
        inputs = {**base_inputs, "operation_type": "send_message",
                  "message_content": "hi"}
        # Force _get_operation_type to yield a value outside the dispatch table.
        # OperationType is a str-Enum; SEND_MESSAGE is remapped to a sentinel that
        # is-not either branch member, so both elifs fall through to the else.
        sentinel = MagicMock(spec=OperationType)
        with patch.object(
            ExecuteTaskUseCase, "_get_operation_type", return_value=sentinel
        ):
            result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.ERROR
        # execute() wraps the ValueError raised at line 280 as VALIDATION_ERROR.
        assert result.outputs["error_code"] == "VALIDATION_ERROR"
        assert "Unsupported operation type" in result.outputs["error"]
        assert result.duration_ms >= 0


# -- execute_task_usecase.py:364-365 — malformed JSON string in card_actions -----
# INVALID INPUT + EDGE. When card_actions is a str, source tries json.loads and on
# JSONDecodeError falls back to [] (line 364-365) -> card built with actions=None.
class TestCardActionsMalformedJson:
    def test_malformed_json_actions_falls_back_to_empty(self, use_case, base_inputs):
        inputs = {
            **base_inputs,
            "operation_type": "send_adaptive_card",
            "card_title": "Title",
            "card_body": "Body",
            "card_actions": "{not valid json",  # triggers JSONDecodeError
        }
        mock_response = {"success": True, "response": "1"}
        with patch.object(
            TeamsWebhookClient, "send_adaptive_card", return_value=mock_response
        ) as mock_send, patch.object(
            TeamsWebhookClient, "build_adaptive_card", wraps=TeamsWebhookClient.build_adaptive_card
        ) as mock_build:
            result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["success"] is True
        mock_send.assert_called_once()
        # Fallback [] -> falsy -> build_adaptive_card called with actions=None.
        assert mock_build.call_args.kwargs["actions"] is None

    def test_valid_json_string_actions_are_parsed(self, use_case, base_inputs):
        # Boundary companion: the happy JSON-string path (json.loads succeeds).
        inputs = {
            **base_inputs,
            "operation_type": "send_adaptive_card",
            "card_title": "Title",
            "card_body": "Body",
            "card_actions": '[{"label": "Go", "url": "https://example.com"}]',
        }
        with patch.object(
            TeamsWebhookClient, "send_adaptive_card", return_value={"success": True}
        ), patch.object(
            TeamsWebhookClient, "build_adaptive_card", wraps=TeamsWebhookClient.build_adaptive_card
        ) as mock_build:
            result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.SUCCESS
        assert mock_build.call_args.kwargs["actions"] == [
            {"label": "Go", "url": "https://example.com"}
        ]


# -- webhook_client.py:202 — non-mapped non-2xx status (500, etc.) ---------------
# FAILURE PATH. Any status not in {200,400,404,429} raises a generic WebhookError
# with error_code="request_failed" (line 202-206).
class TestWebhookClientUnmappedStatus:
    @pytest.fixture
    def client(self):
        return TeamsWebhookClient()

    def test_server_error_raises_generic_webhook_error(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        with patch.object(httpx.Client, "post", return_value=mock_response):
            with pytest.raises(WebhookError) as exc:
                client.send_message(
                    webhook_url=WORKFLOWS_URL,
                    content="Hello!",
                )
        assert exc.value.status_code == 500
        assert exc.value.error_code == "request_failed"
        assert "500" in str(exc.value)

    def test_unmapped_status_propagates_through_usecase(self, use_case, base_inputs):
        # execute() maps a bare WebhookError to its error_code (line 300-303).
        inputs = {**base_inputs, "operation_type": "send_message",
                  "message_content": "Hello"}
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        with patch.object(httpx.Client, "post", return_value=mock_response):
            result = use_case.execute(_command(inputs))
        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "request_failed"


# -- webhook_client.py:214 — generic httpx.HTTPError (non-timeout transport) -----
# FAILURE PATH. A ConnectError is an httpx.HTTPError but not a TimeoutException,
# so it is caught at line 213-217 and re-raised as WebhookError(error_code="http_error").
class TestWebhookClientTransportError:
    @pytest.fixture
    def client(self):
        return TeamsWebhookClient()

    def test_connect_error_wrapped_as_http_error(self, client):
        with patch.object(
            httpx.Client,
            "post",
            side_effect=httpx.ConnectError("connection refused"),
        ):
            with pytest.raises(WebhookError) as exc:
                client.send_message(
                    webhook_url=WORKFLOWS_URL,
                    content="Hello!",
                )
        assert exc.value.error_code == "http_error"
        assert "HTTP error" in str(exc.value)

    def test_transport_error_propagates_through_usecase(self, use_case, base_inputs):
        inputs = {**base_inputs, "operation_type": "send_message",
                  "message_content": "Hello"}
        with patch.object(
            httpx.Client, "post", side_effect=httpx.ConnectError("boom")
        ):
            result = use_case.execute(_command(inputs))
        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "http_error"


# -- Extra grounded edges the existing suite left thin --------------------------
class TestAdditionalEdges:
    def test_credential_wrong_type_raises_missing_webhook_url(self, use_case):
        # EDGE/INVALID INPUT: a non-webhook_url credential means webhook_url == ""
        # -> raises "'webhook_url' is required" (usecase line 260-265).
        inputs = {
            "credential_ref": "c1",
            "__credentials": {"c1": {"type": "bearer", "config": {"token": "x"}}},
            "operation_type": "send_message",
            "message_content": "Hello",
        }
        result = use_case.execute(_command(inputs))
        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_code"] == "VALIDATION_ERROR"
        assert "webhook_url" in result.outputs["error"]

    def test_webhook_url_whitespace_only_is_required(self, use_case):
        # BOUNDARY: a whitespace-only url .strip()s to "" -> required error.
        inputs = {
            "credential_ref": "c1",
            "__credentials": {"c1": {"type": "webhook_url", "config": {"url": "   "}}},
            "operation_type": "send_message",
            "message_content": "Hello",
        }
        result = use_case.execute(_command(inputs))
        assert result.status == TaskStatus.ERROR
        assert "webhook_url" in result.outputs["error"]

    def test_parameters_supply_credential_when_inputs_absent(self, use_case):
        # HAPPY (merge branch): request.parameters carries the credential; inputs
        # override but need not duplicate it. Confirms {**parameters, **inputs} merge.
        params = {
            "credential_ref": "c1",
            "__credentials": {
                "c1": {"type": "webhook_url",
                       "config": {"url": WORKFLOWS_URL}}
            },
        }
        inputs = {"operation_type": "send_message", "message_content": "Hi"}
        with patch.object(
            TeamsWebhookClient, "send_message", return_value={"success": True, "response": "1"}
        ):
            command = ExecuteTaskCommand(
                task_id="merge-1", action="execute", parameters=params, inputs=inputs
            )
            result = use_case.execute(command)
        assert result.status == TaskStatus.SUCCESS
        assert result.duration_ms >= 0

    def test_build_output_defaults_when_response_sparse(self, use_case, base_inputs):
        # EDGE: client returns {} -> _build_output defaults success True, response "".
        inputs = {**base_inputs, "operation_type": "send_message",
                  "message_content": "Hi"}
        with patch.object(TeamsWebhookClient, "send_message", return_value={}):
            result = use_case.execute(_command(inputs))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["success"] is True
        assert result.outputs["response"] == ""
        assert result.outputs["error"] is None

    @pytest.mark.asyncio
    async def test_functions_whitespace_url_rejected(self):
        # BOUNDARY on functions.test_webhook: a whitespace url strips to "" -> required.
        from app.functions import test_webhook as verify_webhook

        params = {
            "credential_ref": "c1",
            "__credentials": {"c1": {"type": "webhook_url", "config": {"url": "   "}}},
        }
        result = await verify_webhook(params)
        assert result["success"] is False
        assert "webhook_url" in result["error"]


class TestAcceptedAtNodeBoundary:
    """202 must surface as a SUCCESSFUL node, not an errored one.

    Reported live on tenant-1: the webhook returned 202 (Power Automate accepted
    the trigger) but the node and the worker both reported an error, because
    _send_to_webhook matched only 200.
    """

    def test_202_yields_success_status(self, use_case, base_inputs):
        inputs = {**base_inputs, "operation_type": "send_message",
                  "message_content": "Hello"}
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.text = ""

        with patch.object(httpx.Client, "post", return_value=mock_response):
            result = use_case.execute(_command(inputs))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["success"] is True
        assert result.outputs["error"] is None
        assert result.outputs["error_code"] is None


def test_webhook_not_found_is_subclass_of_webhook_error():
    # Sanity: exception hierarchy the usecase relies on for ordered except clauses.
    assert issubclass(WebhookNotFoundError, WebhookError)
