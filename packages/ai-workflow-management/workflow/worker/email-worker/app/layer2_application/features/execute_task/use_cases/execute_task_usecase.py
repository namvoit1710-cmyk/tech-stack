"""Execute Task Use Case for Email Worker.

This module handles sending e-mail through two operations:
- send_smtp: an SMTP relay (STARTTLS / SSL / plain)
- send_graph: Microsoft Graph sendMail (OAuth2 client-credentials)
"""

import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

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
from app.layer2_application.services.email_client import (
    EmailAuthError,
    EmailError,
    EmailRateLimitError,
    EmailRecipientsRefusedError,
    GraphEmailClient,
    SmtpEmailClient,
)
from app.layer4_frameworks.config import settings


_SMTP_SECURITY_MODES = ("starttls", "ssl", "none")


# -- Input Schema -------------------------------------------------------------

_INPUT_FIELDS: List[AutoFormField] = [
    # No inline secret fields (T5): SMTP username/password and Graph client
    # credentials come only from the vault credential resolved via credential_ref
    # (see execute()).
    # Operation Type
    select_field(
        key="operation_type",
        label="Operation",
        options=[
            (OperationType.SEND_SMTP.value, "Send via SMTP"),
            (OperationType.SEND_GRAPH.value, "Send via Microsoft Graph"),
        ],
        placeholder="Select operation",
        rules=[ValidationRule(method="required")],
    ),
    # To (always shown)
    AutoFormField(
        key="to_recipients",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "To", "required": True},
            control_props={"placeholder": "alice@example.com, bob@example.com"},
            rules=[ValidationRule(method="required")],
        ),
    ),
    # Cc (always shown)
    AutoFormField(
        key="cc_recipients",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Cc"},
            control_props={"placeholder": "Optional, comma-separated"},
        ),
    ),
    # Bcc (always shown)
    AutoFormField(
        key="bcc_recipients",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Bcc"},
            control_props={"placeholder": "Optional, comma-separated"},
        ),
    ),
    # Subject (always shown)
    AutoFormField(
        key="subject",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Subject", "required": True},
            control_props={"placeholder": "Approval required: change request CR-1042"},
            rules=[ValidationRule(method="required")],
        ),
    ),
    # Body (always shown)
    AutoFormField(
        key="body",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Body", "required": True},
            control_props={
                "placeholder": "Enter the e-mail body here...",
                "rowsLength": 6,
            },
            rules=[ValidationRule(method="required")],
        ),
    ),
    # Body format (always shown)
    select_field(
        key="body_format",
        label="Body Format",
        options=[
            ("text", "Plain Text"),
            ("html", "HTML"),
        ],
        placeholder="Plain Text (default)",
    ),
    # From Address (shown for send_smtp operation)
    AutoFormField(
        key="from_address",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "From Address", "required": True},
            control_props={"placeholder": "noreply@company.com"},
            condition=ConditionConfig(
                field_key="operation_type",
                operator=ConditionOperator.NOT_EQUALS,
                condition_value=OperationType.SEND_SMTP.value,
            ),
        ),
    ),
    # SMTP Host (shown for send_smtp operation)
    AutoFormField(
        key="smtp_host",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={
                "label": "SMTP Host",
                "description": "Defaults to the host of the credential's Base URL",
            },
            control_props={"placeholder": "smtp.office365.com"},
            condition=ConditionConfig(
                field_key="operation_type",
                operator=ConditionOperator.NOT_EQUALS,
                condition_value=OperationType.SEND_SMTP.value,
            ),
        ),
    ),
    # SMTP Port (shown for send_smtp operation)
    AutoFormField(
        key="smtp_port",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={
                "label": "SMTP Port",
                "description": "Defaults to 587 (STARTTLS) or 465 (SSL)",
            },
            control_props={"placeholder": "587"},
            condition=ConditionConfig(
                field_key="operation_type",
                operator=ConditionOperator.NOT_EQUALS,
                condition_value=OperationType.SEND_SMTP.value,
            ),
        ),
    ),
    # SMTP Security (shown for send_smtp operation)
    AutoFormField(
        key="smtp_security",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.SELECT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "SMTP Security"},
            control_props={
                "placeholder": "STARTTLS (default)",
                "options": [
                    {"id": "starttls", "value": "STARTTLS"},
                    {"id": "ssl", "value": "SSL/TLS"},
                    {"id": "none", "value": "None"},
                ],
            },
            condition=ConditionConfig(
                field_key="operation_type",
                operator=ConditionOperator.NOT_EQUALS,
                condition_value=OperationType.SEND_SMTP.value,
            ),
        ),
    ),
    # Sender mailbox (shown for send_graph operation)
    AutoFormField(
        key="sender",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={
                "label": "Sender Mailbox",
                "required": True,
                "description": "Mailbox to send as (userPrincipalName or user id)",
            },
            control_props={"placeholder": "noreply@company.com"},
            condition=ConditionConfig(
                field_key="operation_type",
                operator=ConditionOperator.NOT_EQUALS,
                condition_value=OperationType.SEND_GRAPH.value,
            ),
        ),
    ),
]

EMAIL_INPUT_SCHEMA: List[Dict[str, Any]] = [f.to_dict() for f in _INPUT_FIELDS]


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

EMAIL_OUTPUT_SCHEMA: List[Dict[str, Any]] = [f.to_dict() for f in _OUTPUT_FIELDS]


# -- Helpers ------------------------------------------------------------------


def _parse_recipients(value: Any) -> List[str]:
    """Normalize a recipients input (list or comma/semicolon string) to a list."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [
        address.strip()
        for address in str(value).replace(";", ",").split(",")
        if address.strip()
    ]


# -- Use Case -----------------------------------------------------------------


class ExecuteTaskUseCase:
    """Send e-mail via SMTP relay or Microsoft Graph."""

    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        **kwargs,  # noqa: ARG002
    ):
        self.logger = logger
        self.monitor = monitor
        self._smtp_client = SmtpEmailClient()
        self._graph_client = GraphEmailClient(graph_base_url=settings.GRAPH_BASE_URL)

    def execute(self, request: ExecuteTaskCommand) -> ExecuteTaskResult:
        """Execute the e-mail send operation.

        Args:
            request: The task command with inputs.

        Returns:
            ExecuteTaskResult with operation results.
        """
        self.logger.info(
            "Executing e-mail operation",
            task_id=request.task_id,
            action=request.action,
        )
        self.monitor.track("email_task_requests", 1)

        start = time.monotonic()

        try:
            # Merge parameters with inputs (inputs take precedence)
            inputs = {**request.parameters, **request.inputs}

            operation_type = self._get_operation_type(inputs.get("operation_type"))

            # Auth comes only from the vault credential resolved via credential_ref
            # (T5: no inline secrets). A missing credential fails closed.
            cred = resolve_effective_auth(inputs)
            if not cred:
                raise ValueError(
                    "an e-mail credential is required: set a credential_ref on the node"
                )

            to = _parse_recipients(inputs.get("to_recipients"))
            if not to:
                raise ValueError("'to_recipients' is required")
            cc = _parse_recipients(inputs.get("cc_recipients"))
            bcc = _parse_recipients(inputs.get("bcc_recipients"))

            subject = (inputs.get("subject") or "").strip()
            if not subject:
                raise ValueError("'subject' is required")

            body = inputs.get("body") or ""
            if not str(body).strip():
                raise ValueError("'body' is required")

            body_format = (inputs.get("body_format") or "text").strip().lower()
            if body_format not in ("text", "html"):
                raise ValueError("'body_format' must be 'text' or 'html'")
            html = body_format == "html"

            if operation_type == OperationType.SEND_SMTP:
                result = self._send_smtp(inputs, cred, to, cc, bcc, subject, body, html)
            elif operation_type == OperationType.SEND_GRAPH:
                result = self._send_graph(inputs, cred, to, cc, bcc, subject, body, html)
            else:
                raise ValueError(f"Unsupported operation type: {operation_type}")

            duration_ms = (time.monotonic() - start) * 1000

            self.monitor.track("email_task_duration_ms", duration_ms)
            self.monitor.track("email_task_success", 1)

            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.SUCCESS,
                outputs=result,
                duration_ms=duration_ms,
            )

        except ValueError as e:
            return self._handle_error(request, start, str(e), "VALIDATION_ERROR")
        except EmailAuthError as e:
            return self._handle_error(request, start, str(e), "AUTH_ERROR")
        except EmailRecipientsRefusedError as e:
            return self._handle_error(request, start, str(e), "RECIPIENTS_REFUSED")
        except EmailRateLimitError as e:
            return self._handle_error(request, start, str(e), "RATE_LIMIT_EXCEEDED")
        except EmailError as e:
            return self._handle_error(
                request, start, str(e), e.error_code or "EMAIL_ERROR"
            )
        except Exception as e:
            return self._handle_error(request, start, str(e), "UNKNOWN_ERROR")

    def _send_smtp(
        self,
        inputs: Dict[str, Any],
        cred: Dict[str, Any],
        to: List[str],
        cc: List[str],
        bcc: List[str],
        subject: str,
        body: str,
        html: bool,
    ) -> Dict[str, Any]:
        """Send via the SMTP relay using vault-supplied username/password.

        Args:
            inputs: Task inputs.
            cred: Vault-resolved credential ({type, config}).
            to: To recipients.
            cc: Cc recipients.
            bcc: Bcc recipients.
            subject: Message subject.
            body: Message body.
            html: Send as HTML.

        Returns:
            Dictionary with operation result.
        """
        settings = self._smtp_settings_from_credential(cred)
        username = settings["username"]
        password = settings["password"]

        from_address = (inputs.get("from_address") or "").strip()
        if not from_address:
            raise ValueError("'from_address' is required for send_smtp operation")

        # Server details: a node field overrides the credential; the credential
        # (smtp-basic) is the normal source; otherwise fall back to defaults.
        host = (inputs.get("smtp_host") or "").strip() or settings["host"]
        if not host:
            raise ValueError(
                "'smtp_host' is required (set it on the node or use an SMTP credential)"
            )

        security = (
            (inputs.get("smtp_security") or "").strip().lower()
            or settings["security"]
            or "starttls"
        )
        if security not in _SMTP_SECURITY_MODES:
            raise ValueError(
                f"'smtp_security' must be one of {list(_SMTP_SECURITY_MODES)}"
            )

        raw_port = inputs.get("smtp_port")
        if raw_port in (None, ""):
            port = settings["port"] or (465 if security == "ssl" else 587)
        else:
            try:
                port = int(str(raw_port).strip())
            except ValueError:
                raise ValueError("'smtp_port' must be a number") from None

        response = self._smtp_client.send(
            host=host,
            port=port,
            security=security,
            username=username,
            password=password,
            from_address=from_address,
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            html=html,
        )

        return self._build_output(response)

    def _send_graph(
        self,
        inputs: Dict[str, Any],
        cred: Dict[str, Any],
        to: List[str],
        cc: List[str],
        bcc: List[str],
        subject: str,
        body: str,
        html: bool,
    ) -> Dict[str, Any]:
        """Send via Microsoft Graph using vault-supplied OAuth2 app credentials.

        Args:
            inputs: Task inputs.
            cred: Vault-resolved credential ({type, config}).
            to: To recipients.
            cc: Cc recipients.
            bcc: Bcc recipients.
            subject: Message subject.
            body: Message body.
            html: Send as HTML.

        Returns:
            Dictionary with operation result.
        """
        sender = (inputs.get("sender") or "").strip()
        if not sender:
            raise ValueError("'sender' is required for send_graph operation")

        access_token = self._graph_token_from_credential(cred)

        response = self._graph_client.send_mail(
            access_token=access_token,
            sender=sender,
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            html=html,
        )

        return self._build_output(response)

    def _smtp_settings_from_credential(
        self, cred: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract SMTP settings from the vault credential.

        Supports two credential shapes, both arriving as ``type: "json"`` (the
        control plane exposes JSON-blob credential types verbatim):

        - **smtp-basic** (dedicated): ``{host, port, security, username, password}`` —
          the full server + login, so host/port/security need not be set on the node.
        - **generic-basic-auth** (back-compat): ``{baseUrl, username, password}`` — only
          the login; host is derived from ``baseUrl`` and port/security come from the
          node or defaults.

        Args:
            cred: Vault-resolved credential ({type, config}).

        Returns:
            Dict with keys ``username``, ``password``, ``host``, ``port``, ``security``.
            ``host``/``security`` are ``""`` and ``port`` is ``0`` when the credential
            does not carry them (the caller fills those from the node or defaults).

        Raises:
            ValueError: If the credential carries no SMTP username/password.
        """
        cred_type = cred.get("type")
        config = cred.get("config") or {}

        if cred_type in ("json", "basic") and config.get("username") is not None:
            host = str(config.get("host") or "").strip()
            if not host:
                base_url = (config.get("baseUrl") or "").strip()
                if base_url:
                    host = urlparse(base_url).hostname or ""

            raw_port = config.get("port")
            try:
                port = int(raw_port) if raw_port not in (None, "") else 0
            except (TypeError, ValueError):
                port = 0

            return {
                "username": str(config.get("username") or ""),
                "password": str(config.get("password") or ""),
                "host": host,
                "port": port,
                "security": str(config.get("security") or "").strip().lower(),
            }

        raise ValueError(
            f"credential type '{cred_type}' does not carry an SMTP username/password "
            "(use an SMTP Relay or Generic Basic Auth credential)"
        )

    def _graph_token_from_credential(self, cred: Dict[str, Any]) -> str:
        """Resolve a Graph access token from the credential.

        Accepts the vault's generic-oauth2-app JSON blob (type ``json`` with
        tokenUrl/clientId/clientSecret — runs the client-credentials grant) and a
        pre-issued ``bearer`` token.

        Args:
            cred: Vault-resolved credential ({type, config}).

        Returns:
            The access token string.

        Raises:
            ValueError: If the credential is not usable for Graph.
        """
        cred_type = cred.get("type")
        config = cred.get("config") or {}

        if cred_type == "bearer" and config.get("token"):
            return str(config["token"])

        if cred_type == "json" and config.get("tokenUrl") and config.get("clientId"):
            return self._graph_client.fetch_token(
                token_url=str(config["tokenUrl"]),
                client_id=str(config["clientId"]),
                client_secret=str(config.get("clientSecret") or ""),
                scopes=str(config.get("scopes") or ""),
            )

        raise ValueError(
            f"credential type '{cred_type}' is not usable for Microsoft Graph "
            "(use a Generic OAuth2 Application credential)"
        )

    def _build_output(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Build output dictionary from the client response.

        Args:
            response: Client response.

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
            "E-mail operation failed",
            task_id=request.task_id,
            error=error_message,
            error_code=error_code,
        )
        self.monitor.track("email_task_failures", 1)

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
