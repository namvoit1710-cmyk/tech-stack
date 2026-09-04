import asyncio

import pytest

pytest.importorskip("fastapi")

from smart_service_sdk.layer2_application.interfaces.generation_client_interface import (
    GenerationMessage,
    GenerationRequest,
)
from smart_service_sdk.layer4_frameworks.llm.openai_generation_client import (
    OpenAIGenerationClient,
)
import smart_service_sdk.layer4_frameworks.llm.openai_generation_client as client_module


class _FakeResponse:
    def __init__(
        self,
        *,
        output_text: str = "",
        response_id: str | None = "resp-1",
        output: list[dict[str, object]] | None = None,
    ) -> None:
        self.output_text = output_text
        self.id = response_id
        self.output = output or []

    def model_dump(self) -> dict[str, object]:
        return {
            "id": self.id,
            "output_text": self.output_text,
            "output": self.output,
        }


class _FakeResponsesApi:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []
        self.create_response = _FakeResponse(output_text="hello world")

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self.create_response


class _FakeOpenAI:
    def __init__(self, **kwargs) -> None:
        self.init_kwargs = kwargs
        self.responses = _FakeResponsesApi()


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    fake_openai_client: _FakeOpenAI | None = None,
) -> tuple[OpenAIGenerationClient, _FakeOpenAI]:
    instance = fake_openai_client or _FakeOpenAI()
    monkeypatch.setattr(client_module, "OpenAI", lambda **kwargs: _record_factory_kwargs(instance, **kwargs))
    client = OpenAIGenerationClient(
        api_key="test-key",
        model="gpt-4.1-mini",
        timeout_seconds=15.0,
        max_retries=4,
        retry_backoff_seconds=9.0,
    )
    return client, instance


def _record_factory_kwargs(instance: _FakeOpenAI, **kwargs) -> _FakeOpenAI:
    instance.init_kwargs = kwargs
    return instance


def test_openai_generation_client_generate_maps_request_to_sdk_call(monkeypatch) -> None:
    client, fake_openai_client = _build_client(monkeypatch)

    result = asyncio.run(
        client.generate(
            GenerationRequest(
                messages=(
                    GenerationMessage(role="developer", content="Talk like a pirate."),
                    GenerationMessage(
                        role="user",
                        content="Are semicolons optional in JavaScript?",
                    ),
                ),
                response_format="json_object",
                tools=({"type": "web_search"},),
                tool_choice="required",
            )
        )
    )

    assert fake_openai_client.init_kwargs == {
        "api_key": "test-key",
        "base_url": "https://api.openai.com/v1",
        "timeout": 15.0,
        "max_retries": 4,
    }
    assert fake_openai_client.responses.create_calls == [
        {
            "model": "gpt-4.1-mini",
            "input": (
                GenerationMessage(role="developer", content="Talk like a pirate."),
                GenerationMessage(
                    role="user",
                    content="Are semicolons optional in JavaScript?",
                ),
            ),
            "tools": ({"type": "web_search"},),
        }
    ]
    assert result.output_text == "hello world"
    assert result.response_id == "resp-1"


def test_openai_generation_client_generate_extracts_output_from_output_items(monkeypatch) -> None:
    fake_openai_client = _FakeOpenAI()
    fake_openai_client.responses.create_response = _FakeResponse(
        output_text="",
        output=[
            {
                "content": [
                    {"type": "output_text", "text": "hello "},
                    {"type": "output_text", "text": "world"},
                ]
            }
        ],
    )
    client, _ = _build_client(monkeypatch, fake_openai_client)

    result = asyncio.run(
        client.generate(
            GenerationRequest(
                messages=(GenerationMessage(role="user", content="Hello"),),
            )
        )
    )

    assert result.output_text == "hello world"


def test_openai_generation_client_stream_is_intentionally_disabled(monkeypatch) -> None:
    client, _ = _build_client(monkeypatch)

    async def _consume_stream() -> None:
        async for _event in client.stream(
            GenerationRequest(
                messages=(GenerationMessage(role="user", content="Stream please"),),
            )
        ):
            pass

    with pytest.raises(
        RuntimeError,
        match="OpenAI stream is intentionally disabled",
    ):
        asyncio.run(_consume_stream())
