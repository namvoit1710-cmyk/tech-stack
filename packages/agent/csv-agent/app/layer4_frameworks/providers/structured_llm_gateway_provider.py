from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.layer1_domain.exceptions import LLMServiceError
from app.layer2_application.interfaces.structured_llm_gateway import (
    IStructuredLLMGateway,
)

logger = logging.getLogger(__name__)


class _FlexibleModel(BaseModel):
    """Structured-output model that tolerates provider/tool-specific extras."""

    model_config = ConfigDict(extra="allow")


class ExecutionStepSchema(_FlexibleModel):
    order: int = Field(default=1, ge=1)
    tool: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class ExecutionPlanSchema(_FlexibleModel):
    goal: str = ""
    steps: list[ExecutionStepSchema] = Field(default_factory=list)
    workflow_id: str | None = None


class ColumnRuleSchema(_FlexibleModel):
    rule_name: str = ""
    type: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class ColumnTransformSchema(_FlexibleModel):
    file_path: str = ""
    file_format: str = "csv"
    rules: list[ColumnRuleSchema] = Field(default_factory=list, max_length=100)
    output_format: str | None = None
    error: str | None = None


class RowIdentifierSchema(_FlexibleModel):
    type: str = ""
    index: int | None = None
    expression: str | None = None
    match_values: dict[str, Any] | None = None


class RowOperationSchema(_FlexibleModel):
    operation: str = ""
    row_identifier: RowIdentifierSchema = Field(default_factory=RowIdentifierSchema)
    position: str | None = None
    data: dict[str, Any] | None = None


class RowTransformSchema(_FlexibleModel):
    file_path: str = ""
    file_format: str = "csv"
    operations: list[RowOperationSchema] = Field(default_factory=list, max_length=100)
    use_column_indices: bool = False
    error: str | None = None


def _safe_attr(value: Any) -> Any:
    # Return log-safe scalar diagnostics without exposing secrets.
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return type(value).__name__


def _summarize_llm_service(llm_service: Any) -> dict[str, Any]:
    # Best-effort, secret-safe diagnostics for the configured SDK LLM service.
    diagnostics: dict[str, Any] = {
        "class": f"{type(llm_service).__module__}.{type(llm_service).__name__}",
        "has_acomplete_with_format": callable(
            getattr(llm_service, "acomplete_with_format", None)
        ),
        "has_get_chat_completion": callable(
            getattr(llm_service, "get_chat_completion", None)
        ),
    }

    blocked_fragments = ("key", "secret", "token", "password", "credential")
    interesting_attrs = (
        "provider",
        "model",
        "model_name",
        "_model",
        "api_base",
        "base_url",
        "timeout",
        "_timeout",
        "organization",
        "project",
    )

    for attr_name in interesting_attrs:
        if any(fragment in attr_name.lower() for fragment in blocked_fragments):
            continue
        if hasattr(llm_service, attr_name):
            try:
                diagnostics[attr_name] = _safe_attr(getattr(llm_service, attr_name))
            except Exception as exc:  # noqa: BLE001 - diagnostics must never break runtime
                diagnostics[attr_name] = f"<unreadable:{type(exc).__name__}>"

    client = getattr(llm_service, "client", None) or getattr(
        llm_service, "_client", None
    )
    if client is not None:
        diagnostics["client_class"] = (
            f"{type(client).__module__}.{type(client).__name__}"
        )
        for attr_name in ("base_url", "timeout", "_timeout"):
            if hasattr(client, attr_name):
                try:
                    diagnostics[f"client.{attr_name}"] = _safe_attr(
                        getattr(client, attr_name)
                    )
                except Exception as exc:  # noqa: BLE001
                    diagnostics[f"client.{attr_name}"] = (
                        f"<unreadable:{type(exc).__name__}>"
                    )

    return diagnostics


def _prompt_preview(messages: list[dict[str, str]], *, max_chars: int = 600) -> str:
    rendered: list[str] = []
    for message in messages:
        role = str(message.get("role", ""))
        content = str(message.get("content", ""))
        rendered.append(f"{role}: {content[:max_chars]}")
    preview = "\n---\n".join(rendered)
    return preview[: max_chars * 2]


def _message_char_count(messages: list[dict[str, str]]) -> int:
    return sum(len(str(message.get("content", ""))) for message in messages)


def _model_to_payload(model: BaseModel) -> dict[str, Any]:
    """Dump a Pydantic model to a plain dict before returning to layer 2."""

    return model.model_dump(exclude_none=True)


class SDKStructuredLLMGatewayProvider(IStructuredLLMGateway):
    """Layer-4 adapter for SDK structured LLM completions.

    Pydantic response models stay in layer 4. Application-layer code only sees
    plain dictionaries returned by this port implementation.
    """

    def __init__(self, llm_service: Any) -> None:
        self._llm_service = llm_service

    async def _complete(
        self, *, messages: list[dict[str, str]], response_model: type[BaseModel]
    ) -> BaseModel:
        diagnostics = _summarize_llm_service(self._llm_service)
        response_model_name = getattr(response_model, "__name__", str(response_model))
        complete_with_format = getattr(self._llm_service, "acomplete_with_format", None)

        logger.info(
            "Structured LLM request starting: response_model=%s message_count=%s "
            "message_chars=%s llm_diagnostics=%s prompt_preview=%r",
            response_model_name,
            len(messages),
            _message_char_count(messages),
            diagnostics,
            _prompt_preview(messages),
        )

        if not callable(complete_with_format):
            logger.error(
                "Structured LLM request cannot start: missing acomplete_with_format "
                "response_model=%s llm_diagnostics=%s",
                response_model_name,
                diagnostics,
            )
            raise LLMServiceError(
                "LLM service must expose acomplete_with_format for structured output"
            )

        try:
            model = await complete_with_format(
                messages=messages,
                response_model=response_model,
            )
        except Exception as exc:
            logger.exception(
                "Structured LLM request failed: response_model=%s exception_type=%s "
                "exception=%r cause_type=%s cause=%r context_type=%s context=%r "
                "llm_diagnostics=%s",
                response_model_name,
                type(exc).__name__,
                exc,
                type(exc.__cause__).__name__ if exc.__cause__ else None,
                exc.__cause__,
                type(exc.__context__).__name__ if exc.__context__ else None,
                exc.__context__,
                diagnostics,
            )
            raise

        payload_keys: list[str] = []
        if isinstance(model, BaseModel):
            try:
                payload_keys = sorted(model.model_dump(exclude_none=True).keys())
            except Exception:  # noqa: BLE001 - diagnostics only
                payload_keys = []

        logger.info(
            "Structured LLM request succeeded: response_model=%s result_class=%s "
            "payload_keys=%s",
            response_model_name,
            f"{type(model).__module__}.{type(model).__name__}",
            payload_keys,
        )
        return model

    async def generate_execution_plan(
        self, *, system_prompt: str, user_prompt: str
    ) -> dict[str, Any]:
        model = await self._complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_model=ExecutionPlanSchema,
        )
        return _model_to_payload(model)

    async def generate_column_transform_payload(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        verification_prompt: str,
        file_id: str,
    ) -> dict[str, Any]:
        draft_model = await self._complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_model=ColumnTransformSchema,
        )
        draft_payload = _model_to_payload(draft_model)

        verified_model = await self._complete(
            messages=[
                {"role": "system", "content": verification_prompt},
                {
                    "role": "user",
                    "content": json.dumps(draft_payload, ensure_ascii=False),
                },
            ],
            response_model=ColumnTransformSchema,
        )
        payload = _model_to_payload(verified_model)
        payload["file_path"] = file_id
        return payload

    async def generate_row_transform_payload(
        self, *, system_prompt: str, user_prompt: str, file_id: str
    ) -> dict[str, Any]:
        model = await self._complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_model=RowTransformSchema,
        )
        payload = _model_to_payload(model)
        payload["file_path"] = file_id
        return payload
