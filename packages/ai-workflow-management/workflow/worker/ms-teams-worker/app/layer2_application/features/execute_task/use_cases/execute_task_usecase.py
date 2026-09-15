"""Execute Task Use Case for MS Teams Worker.

This module handles execution of MS Teams operations via Incoming Webhooks:
- Sending messages to channels
- Sending Adaptive Cards to channels
"""

import json
import time
from typing import Any, Dict, List, Optional

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.interfaces.logger_interface import ILogger
from worker_sdk.layer2_application.interfaces.monitor_interface import IMonitor
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
    ExecuteTaskResult,
)
from worker_sdk.layer2_application.services.effective_auth import resolve_effective_auth
from worker_sdk.layer1_domain.auto_form_mapper import (
    AutoFormField,
    ConditionConfig,
    ConditionOperator,
    FieldConfig,
    FieldControl,
    FieldWrapper,
    OutputType,
    ValidationRule,
    select_field,
    text_field,
)
from app.layer1_domain.value_objects.operation_type import OperationType
from app.layer2_application.services.webhook_client import (
    TeamsWebhookClient,
    WebhookError,
    WebhookNotFoundError,
    WebhookRateLimitError,
    webhook_url_from_credential,
)


# -- Input Schema -------------------------------------------------------------

_INPUT_FIELDS: List[AutoFormField] = [
    # T5 hard-remove: the inline webhook_url field is gone — the webhook URL is a secret
    # (it grants posting rights) and now comes only from the vault webhook_url credential
    # resolved via credential_ref (see execute()).
    # Operation Type
    select_field(
        key="operation_type",
        label="Operation",
        options=[
            (OperationType.SEND_MESSAGE.value, "Send Message"),
            (OperationType.SEND_ADAPTIVE_CARD.value, "Send Adaptive Card"),
        ],
        placeholder="Select operation",
        rules=[ValidationRule(method="required")],
    ),
    # Message Title (shown for send_message operation)
    AutoFormField(
        key="message_title",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Message Title"},
            control_props={"placeholder": "Optional title for the message"},
            condition=ConditionConfig(
                field_key="operation_type",
                operator=ConditionOperator.NOT_EQUALS,
                condition_value=OperationType.SEND_MESSAGE.value,
            ),
        ),
    ),
    # Message Content (shown for send_message operation)
    AutoFormField(
        key="message_content",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Message Content", "required": True},
            control_props={
                "placeholder": "Enter your message here (supports markdown)...",
                "rowsLength": 4,
            },
            rules=[ValidationRule(method="required")],
            condition=ConditionConfig(
                field_key="operation_type",
                operator=ConditionOperator.NOT_EQUALS,
                condition_value=OperationType.SEND_MESSAGE.value,
            ),
        ),
    ),
    # Card Title (shown for send_adaptive_card operation)
    AutoFormField(
        key="card_title",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Card Title", "required": True},
            control_props={"placeholder": "Approval Request"},
            rules=[ValidationRule(method="required")],
            condition=ConditionConfig(
                field_key="operation_type",
                operator=ConditionOperator.NOT_EQUALS,
                condition_value=OperationType.SEND_ADAPTIVE_CARD.value,
            ),
        ),
    ),
    # Card Body (shown for send_adaptive_card operation)
    AutoFormField(
        key="card_body",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Card Body", "required": True},
            control_props={
                "placeholder": "Please review and approve this request...",
                "rowsLength": 4,
            },
            rules=[ValidationRule(method="required")],
            condition=ConditionConfig(
                field_key="operation_type",
                operator=ConditionOperator.NOT_EQUALS,
                condition_value=OperationType.SEND_ADAPTIVE_CARD.value,
            ),
        ),
    ),
    # Card Actions (shown for send_adaptive_card operation)
    AutoFormField(
        key="card_actions",
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_wrapper=FieldWrapper.SEQUENCE,
            wrapper_props={
                "label": "Action Buttons (OpenUrl)",
                "isAdd": True,
                "isDelete": True,
            },
            condition=ConditionConfig(
                field_key="operation_type",
                operator=ConditionOperator.NOT_EQUALS,
                condition_value=OperationType.SEND_ADAPTIVE_CARD.value,
            ),
        ),
        fields=[
            AutoFormField(
                key="0",
                output_type=OutputType.OBJECT,
                field_config=FieldConfig(
                    field_wrapper=FieldWrapper.FORM_ITEM,
                    wrapper_props={"isAdd": True, "isDelete": True},
                ),
                fields=[
                    AutoFormField(
                        key="label",
                        output_type=OutputType.STRING,
                        field_config=FieldConfig(
                            field_control=FieldControl.INPUT,
                            field_wrapper=FieldWrapper.FORM_ITEM,
                            wrapper_props={"label": "Button Label"},
                            control_props={"placeholder": "Approve"},
                        ),
                    ),
                    AutoFormField(
                        key="url",
                        output_type=OutputType.STRING,
                        field_config=FieldConfig(
                            field_control=FieldControl.INPUT,
                            field_wrapper=FieldWrapper.FORM_ITEM,
                            wrapper_props={"label": "URL"},
                            control_props={"placeholder": "https://..."},
                        ),
                    ),
                ],
            ),
        ],
    ),
    # Mentions (shown for send_adaptive_card operation) — each entry becomes a
    # working @mention (msteams.entities) appended to the card.
    AutoFormField(
        key="mentions",
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_wrapper=FieldWrapper.SEQUENCE,
            wrapper_props={
                "label": "Mentions (@user)",
                "isAdd": True,
                "isDelete": True,
            },
            condition=ConditionConfig(
                field_key="operation_type",
                operator=ConditionOperator.NOT_EQUALS,
                condition_value=OperationType.SEND_ADAPTIVE_CARD.value,
            ),
        ),
        fields=[
            AutoFormField(
                key="0",
                output_type=OutputType.OBJECT,
                field_config=FieldConfig(
                    field_wrapper=FieldWrapper.FORM_ITEM,
                    wrapper_props={"isAdd": True, "isDelete": True},
                ),
                fields=[
                    AutoFormField(
                        key="name",
                        output_type=OutputType.STRING,
                        field_config=FieldConfig(
                            field_control=FieldControl.INPUT,
                            field_wrapper=FieldWrapper.FORM_ITEM,
                            wrapper_props={"label": "Display Name"},
                            control_props={"placeholder": "Jane Steward"},
                        ),
                    ),
                    AutoFormField(
                        key="id",
                        output_type=OutputType.STRING,
                        field_config=FieldConfig(
                            field_control=FieldControl.INPUT,
                            field_wrapper=FieldWrapper.FORM_ITEM,
                            wrapper_props={"label": "UPN / AAD Id"},
                            control_props={"placeholder": "jane@contoso.com"},
                        ),
                    ),
                ],
            ),
        ],
    ),
]

MS_TEAMS_INPUT_SCHEMA: List[Dict[str, Any]] = [f.to_dict() for f in _INPUT_FIELDS]


# -- Output Schema ------------------------------------------------------------

_OUTPUT_FIELDS: List[AutoFormField] = [
    AutoFormField(
        key="success",
        output_type=OutputType.BOOLEAN,
        field_config=FieldConfig(
            field_control=FieldControl.SWITCH,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Success"},
            control_props={"readonly": True},
        ),
    ),
    text_field(
        key="response",
        label="Response",
        readonly=True,
    ),
    text_field(
        key="error",
        label="Error",
        readonly=True,
    ),
    text_field(
        key="error_code",
        label="Error Code",
        readonly=True,
    ),
]

MS_TEAMS_OUTPUT_SCHEMA: List[Dict[str, Any]] = [f.to_dict() for f in _OUTPUT_FIELDS]


# -- Use Case -----------------------------------------------------------------


class ExecuteTaskUseCase:
    """Execute MS Teams operations via webhook."""

    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        **kwargs,  # noqa: ARG002
    ):
        self.logger = logger
        self.monitor = monitor
        self._client = TeamsWebhookClient()

    def execute(self, request: ExecuteTaskCommand) -> ExecuteTaskResult:
        """Execute the MS Teams operation.

        Args:
            request: The task command with inputs.

        Returns:
            ExecuteTaskResult with operation results.
        """
        self.logger.info(
            "Executing MS Teams operation",
            task_id=request.task_id,
            action=request.action,
        )
        self.monitor.track("ms_teams_task_requests", 1)

        start = time.monotonic()

        try:
            # Merge parameters with inputs (inputs take precedence)
            inputs = {**request.parameters, **request.inputs}

            # T5 hard-remove: the webhook URL comes only from the vault credential
            # resolved via credential_ref. No inline fallback — a missing one raises.
            cred = resolve_effective_auth(inputs)
            webhook_url = webhook_url_from_credential(cred)
            if not webhook_url:
                raise ValueError("'webhook_url' is required")

            # Validate webhook URL format
            if not webhook_url.startswith("https://"):
                raise ValueError("'webhook_url' must be a valid HTTPS URL")

            # Fail fast on retired O365 connector URLs (dead since May 2026) —
            # surface O365_CONNECTOR_RETIRED instead of a silent post failure.
            TeamsWebhookClient.validate_webhook_url(webhook_url)

            # Get operation type
            operation_type = self._get_operation_type(inputs.get("operation_type"))

            # Execute based on operation type
            if operation_type == OperationType.SEND_MESSAGE:
                result = self._send_message(inputs, webhook_url)
            elif operation_type == OperationType.SEND_ADAPTIVE_CARD:
                result = self._send_adaptive_card(inputs, webhook_url)
            else:
                raise ValueError(f"Unsupported operation type: {operation_type}")

            duration_ms = (time.monotonic() - start) * 1000

            self.monitor.track("ms_teams_task_duration_ms", duration_ms)
            self.monitor.track("ms_teams_task_success", 1)

            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.SUCCESS,
                outputs=result,
                duration_ms=duration_ms,
            )

        except ValueError as e:
            return self._handle_error(request, start, str(e), "VALIDATION_ERROR")
        except WebhookNotFoundError as e:
            return self._handle_error(request, start, str(e), "WEBHOOK_NOT_FOUND")
        except WebhookRateLimitError as e:
            return self._handle_error(request, start, str(e), "RATE_LIMIT_EXCEEDED")
        except WebhookError as e:
            return self._handle_error(
                request, start, str(e), e.error_code or "WEBHOOK_ERROR"
            )
        except Exception as e:
            return self._handle_error(request, start, str(e), "UNKNOWN_ERROR")

    def _send_message(
        self, inputs: Dict[str, Any], webhook_url: str
    ) -> Dict[str, Any]:
        """Send a text message to a Teams channel via webhook.

        Args:
            inputs: Task inputs.
            webhook_url: Teams Incoming Webhook URL.

        Returns:
            Dictionary with operation result.
        """
        message_content = inputs.get("message_content", "").strip()
        message_title = inputs.get("message_title", "").strip() or None

        if not message_content:
            raise ValueError(
                "'message_content' is required for send_message operation"
            )

        response = self._client.send_message(
            webhook_url=webhook_url,
            content=message_content,
            title=message_title,
        )

        return self._build_output(response)

    def _send_adaptive_card(
        self, inputs: Dict[str, Any], webhook_url: str
    ) -> Dict[str, Any]:
        """Send an Adaptive Card to a Teams channel via webhook.

        Args:
            inputs: Task inputs.
            webhook_url: Teams Incoming Webhook URL.

        Returns:
            Dictionary with operation result.
        """
        card_title = inputs.get("card_title", "").strip()
        card_body = inputs.get("card_body", "").strip()
        card_actions = inputs.get("card_actions", [])
        mentions = inputs.get("mentions", [])

        if not card_title:
            raise ValueError(
                "'card_title' is required for send_adaptive_card operation"
            )
        if not card_body:
            raise ValueError(
                "'card_body' is required for send_adaptive_card operation"
            )

        # Parse card_actions if it's a JSON string
        if isinstance(card_actions, str):
            try:
                card_actions = json.loads(card_actions)
            except json.JSONDecodeError:
                card_actions = []

        # Parse mentions if it's a JSON string (same tolerance as card_actions)
        if isinstance(mentions, str):
            try:
                mentions = json.loads(mentions)
            except json.JSONDecodeError:
                mentions = []

        # Build the Adaptive Card
        card_payload = TeamsWebhookClient.build_adaptive_card(
            title=card_title,
            body=card_body,
            actions=card_actions if card_actions else None,
            mentions=mentions if mentions else None,
        )

        response = self._client.send_adaptive_card(
            webhook_url=webhook_url,
            card_payload=card_payload,
        )

        return self._build_output(response)

    def _build_output(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Build output dictionary from webhook response.

        Args:
            response: Webhook response.

        Returns:
            Standardized output dictionary.
        """
        return {
            "success": response.get("success", True),
            "response": response.get("response", ""),
            "error": None,
            "error_code": None,
        }

    def _get_operation_type(self, value: Optional[str]) -> OperationType:
        """Get the operation type from input value.

        Args:
            value: Operation type string.

        Returns:
            OperationType enum value.

        Raises:
            ValueError: If operation type is invalid.
        """
        if not value:
            raise ValueError("'operation_type' is required")
        return OperationType.from_string(value)

    def _handle_error(
        self,
        request: ExecuteTaskCommand,
        start: float,
        error_message: str,
        error_code: str,
    ) -> ExecuteTaskResult:
        """Handle task execution error.

        Args:
            request: Original task command.
            start: Start time of execution.
            error_message: Error message.
            error_code: Error code.

        Returns:
            ExecuteTaskResult with error details.
        """
        duration_ms = (time.monotonic() - start) * 1000

        self.logger.error(
            "MS Teams operation failed",
            task_id=request.task_id,
            error=error_message,
            error_code=error_code,
        )
        self.monitor.track("ms_teams_task_failures", 1)

        return ExecuteTaskResult(
            task_id=request.task_id,
            status=TaskStatus.ERROR,
            outputs={
                "success": False,
                "response": None,
                "error": error_message,
                "error_code": error_code,
            },
            duration_ms=duration_ms,
            error=error_message,
        )
