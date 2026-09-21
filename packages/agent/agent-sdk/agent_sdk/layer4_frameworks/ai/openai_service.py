from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Optional, TypeVar

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from agent_sdk.layer2_application.interfaces.chat_completion_service import (
    IChatCompletionService,
)
from agent_sdk.layer2_application.interfaces.llm_usage_recorder import ILLMUsageRecorder
from agent_sdk.layer2_application.services.llm_cost_calculator import LLMCostCalculator
from agent_sdk.layer4_frameworks.ai.usage_tracking_chat_model import (
    UsageTrackingChatModel,
)

logger = logging.getLogger(__name__)
init_chat_model = None

StructuredModelT = TypeVar("StructuredModelT", bound=BaseModel)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_RAW_RESPONSE_PREVIEW_CHARS = 500


def looks_like_openai(provider: Optional[str], model: Optional[str]) -> bool:
    provider_key = (provider or "").replace("-", "_").lower()
    model_key = (model or "").lower()
    if provider_key in {"openai", "azure_openai"}:
        return True
    return model_key.startswith(("gpt-", "o1", "o3", "o4"))


_REASONING_EFFORT_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5")
_OPENAI_STRICT_JSON_SCHEMA_MODEL_PREFIXES = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4.5",
    "gpt-5",
    "o1",
    "o3",
    "o4",
)


def supports_strict_json_schema(provider: Optional[str], model: Optional[str]) -> bool:
    """Return True for OpenAI-style models that support strict JSON schema.

    Non-OpenAI providers should use their LangChain integration default; forcing
    an OpenAI method can regress providers whose native default already works.
    """

    if not looks_like_openai(provider, model):
        return False
    model_key = (model or "").lower()
    return model_key.startswith(_OPENAI_STRICT_JSON_SCHEMA_MODEL_PREFIXES)


def supports_reasoning_effort(provider: Optional[str], model: Optional[str]) -> bool:
    """Return True only for OpenAI-style reasoning models.

    Normal chat models such as gpt-4o-mini reject reasoning_effort /
    reasoning.effort, so the SDK must never send that parameter by default.
    """

    if not looks_like_openai(provider, model):
        return False
    model_key = (model or "").lower()
    return model_key.startswith(_REASONING_EFFORT_MODEL_PREFIXES)


def supports_temperature(provider: Optional[str], model: Optional[str]) -> bool:
    """Return False for OpenAI reasoning models, which reject `temperature`.

    Reasoning models (o1/o3/o4/gpt-5) only accept the default temperature (1) and
    error on any explicit value (e.g. the SDK default 0.01), so the SDK must not
    send `temperature` for them. Normal chat models (gpt-4o, gpt-4o-mini, gpt-4.1)
    accept it. This lets LLM_MODEL be switched to a reasoning model at runtime
    without callers having to strip the parameter themselves.
    """

    return not supports_reasoning_effort(provider, model)


def _normalize_reasoning_effort(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def _pop_reasoning_effort_from_model_kwargs(model_kwargs: dict[str, Any]) -> str | None:
    """Remove legacy/env reasoning kwargs and return a normalized effort value.

    This protects non-reasoning models from accidental config like:
    LLM_MODEL_KWARGS={"reasoning":{"effort":"low"}}
    while still allowing an explicit effort for compatible models.
    """

    effort = _normalize_reasoning_effort(model_kwargs.pop("reasoning_effort", None))
    reasoning = model_kwargs.pop("reasoning", None)
    if effort is None and isinstance(reasoning, Mapping):
        effort = _normalize_reasoning_effort(reasoning.get("effort"))
    return effort


def _response_preview(raw: Any, *, limit: int = _RAW_RESPONSE_PREVIEW_CHARS) -> str:
    text = str(raw).replace("\r", "\\r").replace("\n", "\\n")
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}... [truncated {omitted} chars]"


def _coerce_optional_float(value: Any, *, field_name: str) -> float | None:
    # Normalize optional numeric settings before handing them to providers.
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number, not bool")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            return float(normalized)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a number, got {value!r}") from exc
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be a number, got {type(value).__name__}"
        ) from exc


def _coerce_optional_int(value: Any, *, field_name: str) -> int | None:
    # Normalize optional integer settings before handing them to providers.
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer, not bool")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{field_name} must be an integer, got {value!r}")
        return int(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            parsed = float(normalized)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an integer, got {value!r}") from exc
        if not parsed.is_integer():
            raise ValueError(f"{field_name} must be an integer, got {value!r}")
        return int(parsed)
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be an integer, got {type(value).__name__}"
        ) from exc
    if not parsed.is_integer():
        raise ValueError(f"{field_name} must be an integer, got {value!r}")
    return int(parsed)


class LangChainLLMService(IChatCompletionService):
    """Provider-agnostic LLM service backed by LangChain chat models.

    The old SDK class name was ``OpenAIService``, but the implementation already
    routes through ``langchain.chat_models.init_chat_model``. This service keeps
    that behavior and adds structured Pydantic output for every provider that
    LangChain can initialize.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = "gpt-4o-mini",
        temperature: Optional[float] = 0.01,
        provider: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_retries: Optional[int] = 6,
        model_kwargs: Optional[dict[str, Any]] = None,
        reasoning_effort: Optional[str] = None,
        usage_recorder: ILLMUsageRecorder | None = None,
        usage_cost_calculator: LLMCostCalculator | None = None,
        usage_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if isinstance(api_key, str):
            api_key = api_key.strip() or None
        self.api_key: Optional[str] = api_key
        self.model: Optional[str] = model if model is not None else "gpt-4o-mini"
        self.temperature: Optional[float] = _coerce_optional_float(
            temperature if temperature is not None else 0.01,
            field_name="temperature",
        )
        if isinstance(provider, str):
            provider = provider.strip() or None
        self.provider: Optional[str] = provider
        if isinstance(base_url, str):
            base_url = base_url.strip() or None
        self.base_url: Optional[str] = base_url
        self.timeout: Optional[float] = _coerce_optional_float(
            timeout,
            field_name="timeout",
        )
        self.max_tokens: Optional[int] = _coerce_optional_int(
            max_tokens,
            field_name="max_tokens",
        )
        coerced_max_retries = _coerce_optional_int(
            max_retries,
            field_name="max_retries",
        )
        self.max_retries: int = (
            6 if coerced_max_retries is None else coerced_max_retries
        )
        # Extra provider-specific kwargs are forwarded unless a first-class SDK arg
        # already owns that key.
        self.model_kwargs: dict[str, Any] = dict(model_kwargs or {})
        self.reasoning_effort: str | None = _normalize_reasoning_effort(
            reasoning_effort
        )
        self.usage_recorder = usage_recorder
        self.usage_cost_calculator = usage_cost_calculator or LLMCostCalculator(
            price_table={},
        )
        self.usage_metadata: dict[str, Any] = dict(usage_metadata or {})

        self._raw_chat_client: Optional[Any] = None
        self._chat_client: Optional[Any] = None

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, (dict, list)):
            try:
                return json.dumps(content, ensure_ascii=False)
            except TypeError:
                return str(content)
        return str(content)

    @classmethod
    def _normalize_text_content(cls, content: Any) -> str:
        if isinstance(content, str):
            return content
        # Some providers return rich content blocks, e.g. Anthropic/Gemini.
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, Mapping):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            if parts:
                return "\n".join(parts)
        return cls._content_to_text(content)

    @staticmethod
    def _looks_like_openai(provider: Optional[str], model: Optional[str]) -> bool:
        return looks_like_openai(provider, model)

    @classmethod
    def _parse_json_object(cls, raw: str) -> dict[str, Any]:
        candidate = raw.strip()
        fenced = _JSON_FENCE_RE.search(candidate)
        if fenced:
            candidate = fenced.group(1).strip()

        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start < 0 or end <= start:
                preview = _response_preview(raw)
                raise ValueError(
                    f"LLM response is not valid JSON. Raw response preview: {preview}"
                ) from None
            parsed = json.loads(candidate[start : end + 1])

        if not isinstance(parsed, dict):
            raise ValueError(
                f"Expected LLM response to be a JSON object, got {type(parsed).__name__}"
            )
        return parsed

    @classmethod
    def _coerce_messages(
        cls,
        messages: Sequence[Mapping[str, Any] | Any],
    ) -> list[Any]:
        coerced: list[Any] = []
        for message in messages:
            if isinstance(message, BaseMessage):
                coerced.append(message)
                continue

            if isinstance(message, Mapping):
                role = str(message.get("role", "user")).lower()
                content = cls._content_to_text(message.get("content", ""))
                if role in {"system", "developer"}:
                    coerced.append(SystemMessage(content=content))
                elif role in {"assistant", "ai"}:
                    coerced.append(AIMessage(content=content))
                else:
                    coerced.append(HumanMessage(content=content))
                continue

            coerced.append(message)
        return coerced

    @classmethod
    def _coerce_model_payload(
        cls,
        payload: Any,
        response_model: type[StructuredModelT],
    ) -> StructuredModelT:
        if isinstance(payload, response_model):
            return payload
        if isinstance(payload, BaseModel):
            return response_model.model_validate(payload.model_dump())
        if isinstance(payload, Mapping):
            return response_model.model_validate(dict(payload))

        raw = cls._normalize_text_content(getattr(payload, "content", payload))
        return response_model.model_validate(cls._parse_json_object(raw))

    def _bind_json_mode_if_safe(self, client: Any) -> Any:
        # ``response_format={"type": "json_object"}`` is OpenAI-specific.
        # For other providers, rely on prompt/schema instructions instead.
        if not self._looks_like_openai(self.provider, self.model):
            return client
        try:
            return client.bind(response_format={"type": "json_object"})
        except Exception:
            logger.debug("JSON mode bind failed; continuing without provider bind")
            return client

    def get_chat_client(self) -> Any:
        global init_chat_model
        if init_chat_model is None:
            from langchain.chat_models import init_chat_model as _init_chat_model

            init_chat_model = _init_chat_model
        if self._chat_client is None:
            kwargs: dict[str, Any] = {
                "model_provider": self.provider,
                "timeout": self.timeout,
                "max_tokens": self.max_tokens,
                "max_retries": self.max_retries,
            }
            # Reasoning models (o1/o3/o4/gpt-5) reject an explicit `temperature`;
            # only send it for models that accept it. Lets LLM_MODEL be switched to
            # a reasoning model at runtime without breaking the call.
            if supports_temperature(self.provider, self.model):
                kwargs["temperature"] = self.temperature
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.base_url:
                kwargs["base_url"] = self.base_url
            model_kwargs = dict(self.model_kwargs)
            model_kwargs_reasoning_effort = None
            if looks_like_openai(self.provider, self.model):
                model_kwargs_reasoning_effort = _pop_reasoning_effort_from_model_kwargs(
                    model_kwargs
                )
            resolved_reasoning_effort = (
                self.reasoning_effort or model_kwargs_reasoning_effort
            )
            for key, value in model_kwargs.items():
                if key not in kwargs:
                    kwargs[key] = value
            if resolved_reasoning_effort:
                if supports_reasoning_effort(self.provider, self.model):
                    kwargs["reasoning_effort"] = resolved_reasoning_effort
                else:
                    logger.warning(
                        "Ignoring LLM reasoning effort for non-reasoning model %s. "
                        "Unset LLM_REASONING_EFFORT or use a reasoning-capable model.",
                        self.model,
                    )
            self._raw_chat_client = init_chat_model(self.model, **kwargs)
            if self.usage_recorder is None:
                self._chat_client = self._raw_chat_client
            else:
                self._chat_client = UsageTrackingChatModel(
                    self._raw_chat_client,
                    recorder=self.usage_recorder,
                    cost_calculator=self.usage_cost_calculator,
                    provider=self.provider,
                    model=self.model,
                    operation="chat",
                    default_metadata=self.usage_metadata,
                )
        return self._chat_client

    async def get_chat_completion(
        self, system_prompt: str, user_prompt: str, json_mode: bool = True
    ) -> str:
        if json_mode:
            system_prompt = (
                system_prompt.rstrip()
                + "\n\nReturn only valid JSON. Do not include markdown fences, "
                "comments, or extra prose."
            )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        client = self.get_chat_client()
        if json_mode:
            client = self._bind_json_mode_if_safe(client)
        response = await client.ainvoke(messages)
        return self._normalize_text_content(getattr(response, "content", response))

    def _structured_output_method_candidates(self) -> list[str | None]:
        if not self._looks_like_openai(self.provider, self.model):
            return [None]
        if supports_strict_json_schema(self.provider, self.model):
            return ["json_schema", "function_calling", None]
        return ["function_calling", None]

    async def _try_native_structured_output(
        self,
        *,
        messages: list[Any],
        response_model: type[StructuredModelT],
    ) -> tuple[StructuredModelT | None, bool]:
        client = self.get_chat_client()
        with_structured_output = getattr(client, "with_structured_output", None)
        if not callable(with_structured_output):
            return None, False

        for method in self._structured_output_method_candidates():
            method_label = method or "provider_default"
            try:
                structured_client = (
                    with_structured_output(response_model)
                    if method is None
                    else with_structured_output(response_model, method=method)
                )
                response = await structured_client.ainvoke(messages)
                return self._coerce_model_payload(response, response_model), True
            except (NotImplementedError, AttributeError, TypeError, ValueError) as exc:
                logger.info(
                    "Native structured output method %s is unavailable; "
                    "trying fallback: %s",
                    method_label,
                    exc,
                )
            except ValidationError as exc:
                logger.info(
                    "Native structured output method %s returned invalid payload; "
                    "trying fallback: %s",
                    method_label,
                    exc,
                )
            except Exception:
                logger.warning(
                    "Native structured output method %s failed; trying fallback",
                    method_label,
                    exc_info=True,
                )

        return None, False

    async def acomplete_with_format(
        self,
        *,
        messages: Sequence[Mapping[str, Any] | Any],
        response_model: type[StructuredModelT],
    ) -> StructuredModelT:
        """Return a Pydantic model from an LLM response.

        Primary path uses LangChain ``with_structured_output`` when the provider
        supports it. Fallback path sends the Pydantic JSON schema in the prompt,
        optionally enables OpenAI JSON mode only for OpenAI-compatible models,
        then validates the response with Pydantic.
        """

        langchain_messages = self._coerce_messages(messages)

        native_response, used_native = await self._try_native_structured_output(
            messages=langchain_messages,
            response_model=response_model,
        )
        if used_native and native_response is not None:
            return native_response

        schema = json.dumps(
            response_model.model_json_schema(),
            ensure_ascii=False,
            indent=2,
        )
        instruction = SystemMessage(
            content=(
                "Return exactly one valid JSON object that matches this JSON schema. "
                "Do not include markdown fences, comments, or extra prose.\n\n"
                f"JSON schema:\n{schema}"
            )
        )
        client = self._bind_json_mode_if_safe(self.get_chat_client())
        response = await client.ainvoke([instruction, *langchain_messages])
        raw = self._normalize_text_content(getattr(response, "content", response))

        try:
            parsed = self._parse_json_object(raw)
            return response_model.model_validate(parsed)
        except (ValidationError, ValueError) as exc:
            preview = _response_preview(raw)
            raise ValueError(
                f"LLM response did not match {response_model.__name__}: {exc}. "
                f"Raw response preview: {preview}"
            ) from exc

    def complete_with_format(
        self,
        *,
        messages: Sequence[Mapping[str, Any] | Any],
        response_model: type[StructuredModelT],
    ) -> StructuredModelT:
        """Synchronous wrapper for non-async application code."""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.acomplete_with_format(
                    messages=messages,
                    response_model=response_model,
                )
            )

        raise RuntimeError(
            "complete_with_format() cannot run inside an active event loop. "
            "Use 'await acomplete_with_format(...)' in async code."
        )


class OpenAIService(LangChainLLMService):
    """Backward-compatible alias.

    New code should depend on ``ILLMService`` and create the service through
    ``make_llm_service``. Existing code importing ``OpenAIService`` still works.
    """
