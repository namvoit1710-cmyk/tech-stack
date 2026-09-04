from __future__ import annotations
from collections.abc import AsyncIterator
from typing import Any, cast

from openai import OpenAI

from smart_service_sdk.layer2_application.interfaces.generation_client_interface import (
    GenerationRequest,
    GenerationResponse,
    GenerationStreamEvent,
    IGenerationClient,
)


class OpenAIGenerationClient(IGenerationClient):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
    ):
        del retry_backoff_seconds
        self._model = model
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            max_retries=max(0, int(max_retries)),
        )

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self._validate_request(request)
        response = self._client.responses.create(
            model=self._model,
            tools=cast(Any, list(request.tools)),
            input=cast(
                Any,
                [
                    {
                        "role": message.role,
                        "content": message.content,
                    }
                    for message in request.messages
                ],
            ),
        )
        return GenerationResponse(
            output_text=self._extract_output_text(response),
            response_id=self._extract_response_id(response),
        )

    async def stream(
        self,
        request: GenerationRequest,
    ) -> AsyncIterator[GenerationStreamEvent]:
        del request
        raise RuntimeError(
            "OpenAI stream is intentionally disabled in this sync OpenAI client wrapper."
        )
        yield GenerationStreamEvent(event="done", data="")

    @staticmethod
    def _validate_request(request: GenerationRequest) -> None:
        if request.response_format not in {"text", "json_object"}:
            raise ValueError("Unsupported OpenAI response format")

    @classmethod
    def _extract_output_text(cls, response: object) -> str:
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text:
            return output_text
        if hasattr(response, "output_text") and output_text is not None:
            return str(output_text)

        response_map = cls._object_to_mapping(response)
        mapped_output_text = response_map.get("output_text")
        if isinstance(mapped_output_text, str) and mapped_output_text:
            return mapped_output_text

        output_items = response_map.get("output")
        if isinstance(output_items, list):
            text_parts: list[str] = []
            for output_item in output_items:
                if not isinstance(output_item, dict):
                    continue
                output_item_map = cast(dict[str, object], output_item)
                content_items = output_item_map.get("content")
                if not isinstance(content_items, list):
                    continue
                for content_item in content_items:
                    if not isinstance(content_item, dict):
                        continue
                    content_item_map = cast(dict[str, object], content_item)
                    if content_item_map.get("type") == "output_text":
                        text_parts.append(str(content_item_map.get("text", "")))
            if text_parts:
                return "".join(text_parts)

        raise ValueError("OpenAI response did not include output text")

    @classmethod
    def _extract_response_id(cls, response: object) -> str | None:
        response_id = getattr(response, "id", None)
        if response_id:
            return str(response_id)
        response_map = cls._object_to_mapping(response)
        mapped_id = response_map.get("id")
        return str(mapped_id) if mapped_id else None

    @staticmethod
    def _object_to_mapping(response: object) -> dict[str, object]:
        if isinstance(response, dict):
            return cast(dict[str, object], response)
        model_dump = getattr(response, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, dict):
                return cast(dict[str, object], dumped)
        to_dict = getattr(response, "to_dict", None)
        if callable(to_dict):
            dumped = to_dict()
            if isinstance(dumped, dict):
                return cast(dict[str, object], dumped)
        return {}
