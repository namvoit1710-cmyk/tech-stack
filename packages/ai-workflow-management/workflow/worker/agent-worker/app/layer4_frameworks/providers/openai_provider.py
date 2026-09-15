from typing import Any

import httpx

from app.layer1_domain.entities.ai_completion_request import AiCompletionRequest
from app.layer1_domain.entities.ai_completion_response import AiCompletionResponse
from app.layer1_domain.entities.ai_usage import AiUsage
from app.layer1_domain.value_objects.response_format import ResponseFormat
from app.layer4_frameworks.exceptions import (
    AiProviderAuthenticationError,
    AiProviderConnectionError,
    AiProviderRateLimitError,
    AiProviderResponseError,
)


class OpenAiProvider:
    """IAiProvider implementation for the OpenAI API."""

    BASE_URL: str = "https://api.openai.com/v1"

    def __init__(self, api_key: str) -> None:
        self._api_key: str = api_key

    def chat_completion(self, request: AiCompletionRequest) -> AiCompletionResponse:
        """Send a chat completion request to OpenAI."""
        url: str = f"{self.BASE_URL}/chat/completions"

        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        messages: list[dict[str, str]] = [
            {"role": msg.role.value, "content": msg.content}
            for msg in request.messages
        ]

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "top_p": request.top_p,
        }

        if request.response_format == ResponseFormat.JSON:
            payload["response_format"] = {"type": "json_object"}

        try:
            with httpx.Client(timeout=120) as client:
                response: httpx.Response = client.post(url, json=payload, headers=headers)
        except httpx.ConnectError as exc:
            raise AiProviderConnectionError(f"Failed to connect to OpenAI: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise AiProviderConnectionError(f"OpenAI request timed out: {exc}") from exc

        self._handle_error_status(response)

        data: dict[str, Any] = response.json()
        choice: dict[str, Any] = data["choices"][0]
        usage_data: dict[str, int] = data.get("usage", {})

        return AiCompletionResponse(
            content=choice["message"]["content"],
            model=data.get("model", request.model),
            usage=AiUsage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            ),
            finish_reason=choice.get("finish_reason", "unknown"),
        )

    def _handle_error_status(self, response: httpx.Response) -> None:
        """Raise typed exceptions for known HTTP error codes."""
        if response.status_code == 401:
            raise AiProviderAuthenticationError("Invalid OpenAI API key.")
        if response.status_code == 429:
            raise AiProviderRateLimitError("OpenAI rate limit exceeded.")
        if response.status_code >= 400:
            body: str = response.text
            raise AiProviderResponseError(
                f"OpenAI returned HTTP {response.status_code}: {body}"
            )
