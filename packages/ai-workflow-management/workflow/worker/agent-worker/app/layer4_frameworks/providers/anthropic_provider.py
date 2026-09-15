from typing import Any, Optional

import httpx

from app.layer1_domain.entities.ai_completion_request import AiCompletionRequest
from app.layer1_domain.entities.ai_completion_response import AiCompletionResponse
from app.layer1_domain.entities.ai_usage import AiUsage
from app.layer1_domain.value_objects.ai_role import AiRole
from app.layer4_frameworks.exceptions import (
    AiProviderAuthenticationError,
    AiProviderConnectionError,
    AiProviderRateLimitError,
    AiProviderResponseError,
)


class AnthropicProvider:
    """IAiProvider implementation for the Anthropic Messages API."""

    BASE_URL: str = "https://api.anthropic.com/v1"
    API_VERSION: str = "2023-06-01"

    def __init__(self, api_key: str) -> None:
        self._api_key: str = api_key

    def chat_completion(self, request: AiCompletionRequest) -> AiCompletionResponse:
        """Send a chat completion request to Anthropic."""
        url: str = f"{self.BASE_URL}/messages"

        headers: dict[str, str] = {
            "x-api-key": self._api_key,
            "anthropic-version": self.API_VERSION,
            "Content-Type": "application/json",
        }

        # Anthropic uses a separate 'system' param rather than a system message
        system_text: Optional[str] = None
        messages: list[dict[str, str]] = []

        for msg in request.messages:
            if msg.role == AiRole.SYSTEM:
                system_text = msg.content
            else:
                messages.append({"role": msg.role.value, "content": msg.content})

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "top_p": request.top_p,
        }

        if system_text is not None:
            payload["system"] = system_text

        try:
            with httpx.Client(timeout=120) as client:
                response: httpx.Response = client.post(url, json=payload, headers=headers)
        except httpx.ConnectError as exc:
            raise AiProviderConnectionError(f"Failed to connect to Anthropic: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise AiProviderConnectionError(f"Anthropic request timed out: {exc}") from exc

        self._handle_error_status(response)

        data: dict[str, Any] = response.json()
        content_blocks: list[dict[str, Any]] = data.get("content", [])
        content_text: str = self._extract_text(content_blocks)
        usage_data: dict[str, int] = data.get("usage", {})

        prompt_tokens: int = usage_data.get("input_tokens", 0)
        completion_tokens: int = usage_data.get("output_tokens", 0)

        return AiCompletionResponse(
            content=content_text,
            model=data.get("model", request.model),
            usage=AiUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            finish_reason=data.get("stop_reason", "unknown"),
        )

    def _extract_text(self, content_blocks: list[dict[str, Any]]) -> str:
        """Extract text from Anthropic content blocks."""
        parts: list[str] = []
        for block in content_blocks:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)

    def _handle_error_status(self, response: httpx.Response) -> None:
        """Raise typed exceptions for known HTTP error codes."""
        if response.status_code == 401:
            raise AiProviderAuthenticationError("Invalid Anthropic API key.")
        if response.status_code == 429:
            raise AiProviderRateLimitError("Anthropic rate limit exceeded.")
        if response.status_code >= 400:
            body: str = response.text
            raise AiProviderResponseError(
                f"Anthropic returned HTTP {response.status_code}: {body}"
            )
