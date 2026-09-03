from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from agent_sdk.layer4_frameworks.ai.openai_service import LangChainLLMService


class _StructuredReply(BaseModel):
    answer: str


class _StructuredClient:
    async def ainvoke(self, messages: list[Any]) -> _StructuredReply:
        return _StructuredReply(answer="ok")


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def with_structured_output(
        self,
        response_model: type[BaseModel],
        **kwargs: Any,
    ) -> _StructuredClient:
        self.calls.append(dict(kwargs))
        return _StructuredClient()


class _RejectJsonSchemaClient(_RecordingClient):
    def with_structured_output(
        self,
        response_model: type[BaseModel],
        **kwargs: Any,
    ) -> _StructuredClient:
        self.calls.append(dict(kwargs))
        if kwargs.get("method") == "json_schema":
            raise ValueError("json_schema not supported by this integration")
        return _StructuredClient()


def _run_structured(
    service: LangChainLLMService,
    client: _RecordingClient,
) -> _StructuredReply:
    service.get_chat_client = lambda: client  # type: ignore[method-assign]
    return asyncio.run(
        service.acomplete_with_format(
            messages=[{"role": "user", "content": "return ok"}],
            response_model=_StructuredReply,
        )
    )


def test_max_retries_preserves_explicit_zero() -> None:
    assert LangChainLLMService(max_retries=0).max_retries == 0
    assert LangChainLLMService(max_retries="0").max_retries == 0


def test_max_retries_defaults_only_when_none_or_blank() -> None:
    assert LangChainLLMService(max_retries=None).max_retries == 6
    assert LangChainLLMService(max_retries="   ").max_retries == 6


def test_native_structured_output_uses_provider_default_for_non_openai() -> None:
    client = _RecordingClient()
    service = LangChainLLMService(
        provider="anthropic",
        model="claude-3-5-sonnet-latest",
    )

    result = _run_structured(service, client)

    assert result.answer == "ok"
    assert client.calls == [{}]


def test_native_structured_output_prefers_json_schema_for_openai() -> None:
    client = _RecordingClient()
    service = LangChainLLMService(provider="openai", model="gpt-4o-mini")

    result = _run_structured(service, client)

    assert result.answer == "ok"
    assert client.calls == [{"method": "json_schema"}]


def test_native_structured_output_falls_back_to_function_calling() -> None:
    client = _RejectJsonSchemaClient()
    service = LangChainLLMService(provider="openai", model="gpt-4o-mini")

    result = _run_structured(service, client)

    assert result.answer == "ok"
    assert client.calls == [
        {"method": "json_schema"},
        {"method": "function_calling"},
    ]
