from __future__ import annotations

import inspect
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any

from agent_sdk.layer1_domain.entities.llm_usage import LLMUsageRecord
from agent_sdk.layer2_application.interfaces.llm_usage_recorder import ILLMUsageRecorder
from agent_sdk.layer2_application.services.llm_cost_calculator import LLMCostCalculator


class _TokenEstimator:
    def __init__(self, model: str | None = None) -> None:
        self._model = model or ""
        self._encoding = None

    def _get_encoding(self) -> Any:
        if self._encoding is not None:
            return self._encoding
        try:
            import tiktoken

            try:
                self._encoding = tiktoken.encoding_for_model(self._model)
            except Exception:
                self._encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self._encoding = False
        return self._encoding

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        encoding = self._get_encoding()
        if encoding is False:
            return max(1, len(text) // 4)
        return len(encoding.encode(text))

    def count_payload(self, payload: Any) -> int:
        if payload is None:
            return 0
        if isinstance(payload, str):
            return self.count_text(payload)
        if isinstance(payload, Mapping):
            return self.count_text(_json_dumps(payload))
        if isinstance(payload, Sequence) and not isinstance(
            payload, (str, bytes, bytearray)
        ):
            total = 0
            for item in payload:
                total += 4
                total += self.count_payload(_message_content(item))
            return total
        return self.count_text(str(payload))


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _message_content(message: Any) -> Any:
    if isinstance(message, Mapping):
        return message.get("content", "")
    return getattr(message, "content", message)


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
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
    if isinstance(content, Mapping):
        return _json_dumps(content)
    return str(content)


def _extract_usage(response: Any) -> tuple[int, int, int] | None:
    candidates: list[Any] = []
    for attr in ("usage_metadata", "response_metadata", "llm_output"):
        value = getattr(response, attr, None)
        if value:
            candidates.append(value)
    if isinstance(response, Mapping):
        candidates.append(response)

    for candidate in candidates:
        usage = candidate
        if isinstance(candidate, Mapping):
            usage = (
                candidate.get("usage_metadata")
                or candidate.get("token_usage")
                or candidate.get("usage")
                or candidate
            )
        if not isinstance(usage, Mapping):
            continue
        prompt_tokens = _first_int(
            usage,
            "prompt_tokens",
            "input_tokens",
            "prompt_token_count",
            "input_token_count",
        )
        completion_tokens = _first_int(
            usage,
            "completion_tokens",
            "output_tokens",
            "candidates_token_count",
            "output_token_count",
        )
        total_tokens = _first_int(usage, "total_tokens", "total_token_count")
        if prompt_tokens is None and completion_tokens is None and total_tokens is None:
            continue
        prompt = prompt_tokens or 0
        completion = completion_tokens or 0
        total = total_tokens if total_tokens is not None else prompt + completion
        return prompt, completion, total
    return None


def _first_int(mapping: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _extract_config_metadata(config: Any) -> dict[str, Any]:
    if not isinstance(config, Mapping):
        return {}
    metadata = config.get("metadata")
    configurable = config.get("configurable")
    merged: dict[str, Any] = {}
    if isinstance(metadata, Mapping):
        merged.update(metadata)
    if isinstance(configurable, Mapping):
        for key in (
            "agent_type",
            "agent_id",
            "conversation_id",
            "conv_id",
            "thread_id",
            "correlation_id",
        ):
            if key in configurable and key not in merged:
                merged[key] = configurable[key]
    return merged


def _callable_accepts_kwarg(fn: Any, name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return True
    if name in params:
        return True
    return any(param.kind is inspect.Parameter.VAR_KEYWORD for param in params.values())


def _call_with_optional_config(
    fn: Any, input_payload: Any, config: Any, kwargs: dict[str, Any]
) -> Any:
    if _callable_accepts_kwarg(fn, "config"):
        return fn(input_payload, config=config, **kwargs)
    return fn(input_payload, **kwargs)


async def _acall_with_optional_config(
    fn: Any,
    input_payload: Any,
    config: Any,
    kwargs: dict[str, Any],
) -> Any:
    if _callable_accepts_kwarg(fn, "config"):
        return await fn(input_payload, config=config, **kwargs)
    return await fn(input_payload, **kwargs)


class UsageTrackingChatModel:
    """Small proxy that records usage around LangChain chat-model invocations."""

    def __init__(
        self,
        wrapped: Any,
        *,
        recorder: ILLMUsageRecorder,
        cost_calculator: LLMCostCalculator,
        provider: str | None,
        model: str | None,
        operation: str = "chat",
        default_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._wrapped = wrapped
        self._recorder = recorder
        self._cost_calculator = cost_calculator
        self._provider = provider or ""
        self._model = model or ""
        self._operation = operation
        self._default_metadata = dict(default_metadata or {})
        self._token_estimator = _TokenEstimator(self._model)

    @property
    def wrapped(self) -> Any:
        return self._wrapped

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def bind(self, **kwargs: Any) -> "UsageTrackingChatModel":
        return self._wrap(self._wrapped.bind(**kwargs), operation=self._operation)

    def bind_tools(self, tools: Any, **kwargs: Any) -> "UsageTrackingChatModel":
        return self._wrap(
            self._wrapped.bind_tools(tools, **kwargs),
            operation="tool_agent",
        )

    def with_structured_output(
        self, *args: Any, **kwargs: Any
    ) -> "UsageTrackingChatModel":
        return self._wrap(
            self._wrapped.with_structured_output(*args, **kwargs),
            operation="structured_output",
        )

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            response = _call_with_optional_config(
                self._wrapped.invoke, input, config, kwargs
            )
        except Exception as exc:
            self._record_failure(input, config, started, exc)
            raise
        self._record_success(input, config, started, response)
        return response

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            response = await _acall_with_optional_config(
                self._wrapped.ainvoke, input, config, kwargs
            )
        except Exception as exc:
            self._record_failure(input, config, started, exc)
            raise
        self._record_success(input, config, started, response)
        return response

    def batch(self, inputs: list[Any], config: Any = None, **kwargs: Any) -> list[Any]:
        return [self.invoke(item, config=config, **kwargs) for item in inputs]

    async def abatch(
        self,
        inputs: list[Any],
        config: Any = None,
        **kwargs: Any,
    ) -> list[Any]:
        return [await self.ainvoke(item, config=config, **kwargs) for item in inputs]

    def _wrap(self, wrapped: Any, *, operation: str) -> "UsageTrackingChatModel":
        return UsageTrackingChatModel(
            wrapped,
            recorder=self._recorder,
            cost_calculator=self._cost_calculator,
            provider=self._provider,
            model=self._model,
            operation=operation,
            default_metadata=self._default_metadata,
        )

    def _record_success(
        self,
        input_payload: Any,
        config: Any,
        started: float,
        response: Any,
    ) -> None:
        usage = _extract_usage(response)
        estimated = usage is None
        if usage is None:
            prompt_tokens = self._token_estimator.count_payload(input_payload)
            completion_tokens = self._token_estimator.count_payload(
                _message_content(response)
            )
            total_tokens = prompt_tokens + completion_tokens
        else:
            prompt_tokens, completion_tokens, total_tokens = usage

        self._record(
            config=config,
            started=started,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_tokens=estimated,
            success=True,
            error_type=None,
        )

    def _record_failure(
        self,
        input_payload: Any,
        config: Any,
        started: float,
        exc: Exception,
    ) -> None:
        prompt_tokens = self._token_estimator.count_payload(input_payload)
        self._record(
            config=config,
            started=started,
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            total_tokens=prompt_tokens,
            estimated_tokens=True,
            success=False,
            error_type=type(exc).__name__,
        )

    def _record(
        self,
        *,
        config: Any,
        started: float,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        estimated_tokens: bool,
        success: bool,
        error_type: str | None,
    ) -> None:
        metadata = dict(self._default_metadata)
        metadata.update(_extract_config_metadata(config))
        thread_id = str(metadata.get("thread_id") or metadata.get("conv_id") or "")
        conversation_id = str(
            metadata.get("conversation_id") or metadata.get("conv_id") or thread_id
        )
        costs = self._cost_calculator.calculate(
            provider=self._provider,
            model=self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        self._recorder.record(
            LLMUsageRecord(
                agent_type=str(metadata.get("agent_type") or ""),
                agent_id=str(metadata.get("agent_id") or ""),
                conversation_id=conversation_id,
                thread_id=thread_id,
                correlation_id=str(metadata.get("correlation_id") or ""),
                provider=self._provider,
                model=self._model,
                operation=self._operation,
                prompt_tokens=max(prompt_tokens, 0),
                completion_tokens=max(completion_tokens, 0),
                total_tokens=max(total_tokens, 0),
                estimated_tokens=estimated_tokens,
                input_cost=float(costs["input_cost"]),
                output_cost=float(costs["output_cost"]),
                total_cost=float(costs["total_cost"]),
                currency=str(costs["currency"]),
                latency_ms=int((time.perf_counter() - started) * 1000),
                success=success,
                error_type=error_type,
            )
        )
