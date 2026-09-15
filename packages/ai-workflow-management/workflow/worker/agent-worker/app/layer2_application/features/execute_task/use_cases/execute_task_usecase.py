import json
import time
from typing import Any, Optional

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.interfaces.logger_interface import ILogger
from worker_sdk.layer2_application.interfaces.monitor_interface import IMonitor
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
    ExecuteTaskResult,
)
from worker_sdk.layer2_application.services.effective_auth import resolve_effective_auth
from worker_sdk.layer1_domain.auto_form_mapper import (
    AutoFormField, FieldConfig, ValidationRule, OutputType,
    FieldControl, FieldWrapper,
    text_field, number_field, textarea_field, select_field,
    array_field,
)
from app.layer1_domain.value_objects.ai_provider import AiProvider
from app.layer1_domain.value_objects.ai_role import AiRole
from app.layer1_domain.value_objects.response_format import ResponseFormat
from app.layer1_domain.entities.ai_message import AiMessage
from app.layer1_domain.entities.ai_completion_request import AiCompletionRequest
from app.layer2_application.interfaces.ai_provider_interface import IAiProvider


# -- Input Schema -------------------------------------------------------------

_INPUT_FIELDS: list[AutoFormField] = [
    select_field(
        "provider", "AI Provider",
        options=[
            (AiProvider.OPENAI.value, "OpenAI"),
        ],
        placeholder="Select AI provider",
        rules=[ValidationRule(method="required")],
    ),
    text_field(
        "model", "Model",
        placeholder="gpt-4o",
        rules=[ValidationRule(method="required")],
    ),
    # T5 hard-remove: the inline api_key field is gone — the key now comes only from the
    # vault api_key credential resolved via credential_ref (see execute()).
    textarea_field(
        "system_prompt", "System Prompt",
        placeholder="You are a helpful assistant.",
    ),
    textarea_field(
        "user_prompt", "User Prompt",
        placeholder="Summarize the following text: {{input_text}}",
        rules=[ValidationRule(method="required")],
    ),
    array_field(
        "prompt_variables", "Prompt Variables",
        item_fields=[
            text_field(
                "variable_name", "Variable Name",
                placeholder="input_text",
                rules=[ValidationRule(method="required")],
            ),
            textarea_field(
                "variable_value", "Variable Value",
                placeholder="Enter value",
                rules=[ValidationRule(method="required")],
            ),
        ],
    ),
    array_field(
        "conversation_history", "Conversation History",
        item_fields=[
            select_field(
                "role", "Role",
                options=[
                    (AiRole.USER.value, "User"),
                    (AiRole.ASSISTANT.value, "Assistant"),
                ],
                placeholder="Select role",
                rules=[ValidationRule(method="required")],
            ),
            textarea_field(
                "content", "Content",
                placeholder="Message content",
                rules=[ValidationRule(method="required")],
            ),
        ],
    ),
    number_field(
        "temperature", "Temperature",
        placeholder="0.7",
        default=0.7,
        rules=[
            ValidationRule(method="min", value=0.0, message="Minimum temperature is 0"),
            ValidationRule(method="max", value=2.0, message="Maximum temperature is 2"),
        ],
    ),
    number_field(
        "max_tokens", "Max Tokens",
        placeholder="1024",
        default=1024,
        rules=[
            ValidationRule(method="min", value=1, message="Minimum is 1 token"),
            ValidationRule(method="max", value=128000, message="Maximum is 128000 tokens"),
        ],
    ),
    number_field(
        "top_p", "Top P",
        placeholder="1.0",
        default=1.0,
        rules=[
            ValidationRule(method="min", value=0.0, message="Minimum top_p is 0"),
            ValidationRule(method="max", value=1.0, message="Maximum top_p is 1"),
        ],
    ),
    select_field(
        "response_format", "Response Format",
        options=[
            (ResponseFormat.TEXT.value, "Text"),
            (ResponseFormat.JSON.value, "JSON"),
        ],
        placeholder="Select format",
    ),
]

AGENT_INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in _INPUT_FIELDS]


# -- Output Schema ------------------------------------------------------------

_OUTPUT_FIELDS: list[AutoFormField] = [
    textarea_field(
        "response_content", "Response Content",
        rules=[ValidationRule(method="required")],
    ),
    text_field(
        "model_used", "Model Used",
        rules=[ValidationRule(method="required")],
    ),
    text_field("finish_reason", "Finish Reason"),
    number_field("prompt_tokens", "Prompt Tokens"),
    number_field("completion_tokens", "Completion Tokens"),
    number_field("total_tokens", "Total Tokens"),
    text_field("provider_used", "Provider Used"),
    number_field(
        "elapsed_ms", "Elapsed Time (ms)",
        rules=[ValidationRule(method="required")],
    ),
]

AGENT_OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in _OUTPUT_FIELDS]


# -- Use Case -----------------------------------------------------------------

class ExecuteTaskUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        ai_provider_factory: Optional["AiProviderFactory"] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.monitor = monitor
        self.ai_provider_factory = ai_provider_factory

    def execute(self, request: ExecuteTaskCommand) -> ExecuteTaskResult:
        self.logger.info(
            "Executing agent task",
            task_id=request.task_id,
            action=request.action,
        )
        self.monitor.track("agent_task_requests", 1)

        start = time.monotonic()

        try:
            inputs: dict[str, Any] = request.inputs

            # -- Resolve provider --
            provider_name: str = inputs.get("provider", AiProvider.OPENAI.value)
            # T5 hard-remove: the api_key comes only from the vault credential resolved
            # via credential_ref. No inline fallback — a missing/empty credential raises.
            cred = resolve_effective_auth(inputs)
            if cred and cred.get("type") == "api_key":
                api_key = (cred.get("config", {}).get("value") or "").strip()
            else:
                api_key = ""
            if not api_key:
                raise ValueError("'api_key' is required.")

            ai_provider: IAiProvider = self._resolve_provider(provider_name, api_key)

            # -- Resolve model --
            model: str = inputs.get("model", "")
            if not model:
                raise ValueError("'model' is required.")

            # -- Build messages --
            messages: list[AiMessage] = self._build_messages(inputs)

            # -- Resolve parameters --
            temperature: float = float(inputs.get("temperature", 0.7))
            max_tokens: int = int(inputs.get("max_tokens", 1024))
            top_p: float = float(inputs.get("top_p", 1.0))

            # -- Validate parameter ranges --
            if not (0.0 <= temperature <= 2.0):
                raise ValueError(f"temperature must be between 0.0 and 2.0, got {temperature}")
            if not (1 <= max_tokens <= 128000):
                raise ValueError(f"max_tokens must be between 1 and 128000, got {max_tokens}")
            if not (0.0 <= top_p <= 1.0):
                raise ValueError(f"top_p must be between 0.0 and 1.0, got {top_p}")
            response_format_str: str = inputs.get("response_format", ResponseFormat.TEXT.value)
            response_format: ResponseFormat = ResponseFormat(response_format_str)

            # -- Build request --
            completion_request = AiCompletionRequest(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                response_format=response_format,
            )

            # -- Execute --
            self.logger.info(
                f"Calling {provider_name} with model {model}",
                temperature=temperature,
                max_tokens=max_tokens,
            )
            completion_response = ai_provider.chat_completion(completion_request)

            duration_ms: float = (time.monotonic() - start) * 1000

            self.monitor.track("agent_task_duration_ms", duration_ms)
            self.monitor.track("agent_task_success", 1)
            self.monitor.track("agent_task_total_tokens", float(completion_response.usage.total_tokens))

            # -- Parse JSON if requested --
            response_content: str = completion_response.content
            if response_format == ResponseFormat.JSON:
                response_content = self._ensure_valid_json(response_content)

            outputs: dict[str, Any] = {
                "response_content": response_content,
                "model_used": completion_response.model,
                "finish_reason": completion_response.finish_reason,
                "prompt_tokens": completion_response.usage.prompt_tokens,
                "completion_tokens": completion_response.usage.completion_tokens,
                "total_tokens": completion_response.usage.total_tokens,
                "provider_used": provider_name,
                "elapsed_ms": duration_ms,
            }

            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.SUCCESS,
                outputs=outputs,
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = (time.monotonic() - start) * 1000
            self.logger.error("Agent task failed", task_id=request.task_id, error=str(e))
            self.monitor.track("agent_task_error", 1)

            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.ERROR,
                error=str(e),
                duration_ms=duration_ms,
            )

    def _resolve_provider(self, provider_name: str, api_key: str) -> IAiProvider:
        """Resolve the AI provider implementation by name."""
        if self.ai_provider_factory is not None:
            return self.ai_provider_factory.create(provider_name, api_key)

        # Inline fallback when no factory is injected
        from app.layer4_frameworks.providers.openai_provider import OpenAiProvider

        providers: dict[str, type] = {
            AiProvider.OPENAI.value: OpenAiProvider
        }

        provider_cls = providers.get(provider_name)
        if provider_cls is None:
            raise ValueError(f"Unsupported AI provider: '{provider_name}'")

        return provider_cls(api_key=api_key)

    def _build_messages(self, inputs: dict[str, Any]) -> list[AiMessage]:
        """Build the full message list from system prompt, history, and user prompt."""
        messages: list[AiMessage] = []

        # System prompt
        system_prompt: str = inputs.get("system_prompt", "").strip()
        if system_prompt:
            messages.append(AiMessage(role=AiRole.SYSTEM, content=system_prompt))

        # Conversation history
        history: list[dict[str, str]] = inputs.get("conversation_history", [])
        for entry in history:
            role_str: str = entry.get("role", AiRole.USER.value)
            content: str = entry.get("content", "")
            if content:
                role = AiRole(role_str)
                messages.append(AiMessage(role=role, content=content))

        # User prompt with variable substitution
        user_prompt: str = inputs.get("user_prompt", "")
        if not user_prompt:
            raise ValueError("'user_prompt' is required.")

        user_prompt = self._substitute_variables(user_prompt, inputs.get("prompt_variables", []))
        messages.append(AiMessage(role=AiRole.USER, content=user_prompt))

        return messages

    def _substitute_variables(self, template: str, variables: list[dict[str, str]]) -> str:
        """Replace {{variable_name}} placeholders in the template with variable values."""
        result: str = template
        for var in variables:
            name: str = var.get("variable_name", "")
            value: str = var.get("variable_value", "")
            if name:
                result = result.replace("{{" + name + "}}", value)
        return result

    def _ensure_valid_json(self, content: str) -> str:
        """Validate and re-serialize JSON content. Returns the content as-is if valid."""
        try:
            parsed = json.loads(content)
            return json.dumps(parsed)
        except json.JSONDecodeError:
            return content


# -- Provider Factory (typed) -------------------------------------------------

class AiProviderFactory:
    """Factory that creates IAiProvider instances by provider name."""

    def __init__(self, provider_registry: dict[str, type]) -> None:
        self._registry: dict[str, type] = provider_registry

    def create(self, provider_name: str, api_key: str) -> IAiProvider:
        """Create an AI provider instance for the given provider name."""
        provider_cls = self._registry.get(provider_name)
        if provider_cls is None:
            raise ValueError(f"Unsupported AI provider: '{provider_name}'")
        return provider_cls(api_key=api_key)
