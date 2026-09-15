import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.layer1_domain.entities.ai_completion_request import AiCompletionRequest
from app.layer1_domain.entities.ai_message import AiMessage
from app.layer1_domain.value_objects.ai_role import AiRole
from app.layer1_domain.value_objects.response_format import ResponseFormat
from app.layer4_frameworks.exceptions import (
    AiProviderAuthenticationError,
    AiProviderConnectionError,
    AiProviderRateLimitError,
    AiProviderResponseError,
)
from app.layer4_frameworks.providers.openai_provider import OpenAiProvider


def _base_request(
    messages=None,
    model: str = "gpt-4o",
    response_format: ResponseFormat = ResponseFormat.TEXT,
) -> AiCompletionRequest:
    if messages is None:
        messages = [AiMessage(role=AiRole.USER, content="Hello")]
    return AiCompletionRequest(
        messages=messages,
        model=model,
        temperature=0.7,
        max_tokens=1024,
        top_p=1.0,
        response_format=response_format,
    )


def _mock_httpx_response(
    status_code: int = 200,
    json_data: dict = None,
    text: str = "",
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


# ── OpenAI Provider ──────────────────────────────────────────────────────────


class TestOpenAiProvider:
    @patch("app.layer4_frameworks.providers.openai_provider.httpx.Client")
    def test_successful_completion(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _mock_httpx_response(
            status_code=200,
            json_data={
                "choices": [{"message": {"content": "Hi there!"}, "finish_reason": "stop"}],
                "model": "gpt-4o",
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            },
        )
        mock_client_cls.return_value = mock_client

        provider = OpenAiProvider(api_key="sk-test")
        result = provider.chat_completion(_base_request())

        assert result.content == "Hi there!"
        assert result.model == "gpt-4o"
        assert result.finish_reason == "stop"
        assert result.usage.prompt_tokens == 5
        assert result.usage.completion_tokens == 3
        assert result.usage.total_tokens == 8

    @patch("app.layer4_frameworks.providers.openai_provider.httpx.Client")
    def test_json_response_format(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _mock_httpx_response(
            status_code=200,
            json_data={
                "choices": [{"message": {"content": '{"a":1}'}, "finish_reason": "stop"}],
                "model": "gpt-4o",
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            },
        )
        mock_client_cls.return_value = mock_client

        provider = OpenAiProvider(api_key="sk-test")
        provider.chat_completion(_base_request(response_format=ResponseFormat.JSON))

        call_kwargs = mock_client.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["response_format"] == {"type": "json_object"}

    @patch("app.layer4_frameworks.providers.openai_provider.httpx.Client")
    def test_auth_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _mock_httpx_response(status_code=401, text="Unauthorized")
        mock_client_cls.return_value = mock_client

        provider = OpenAiProvider(api_key="bad-key")
        with pytest.raises(AiProviderAuthenticationError):
            provider.chat_completion(_base_request())

    @patch("app.layer4_frameworks.providers.openai_provider.httpx.Client")
    def test_rate_limit_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _mock_httpx_response(status_code=429, text="Rate limited")
        mock_client_cls.return_value = mock_client

        provider = OpenAiProvider(api_key="sk-test")
        with pytest.raises(AiProviderRateLimitError):
            provider.chat_completion(_base_request())

    @patch("app.layer4_frameworks.providers.openai_provider.httpx.Client")
    def test_server_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _mock_httpx_response(status_code=500, text="Internal error")
        mock_client_cls.return_value = mock_client

        provider = OpenAiProvider(api_key="sk-test")
        with pytest.raises(AiProviderResponseError, match="500"):
            provider.chat_completion(_base_request())

    @patch("app.layer4_frameworks.providers.openai_provider.httpx.Client")
    def test_connection_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client_cls.return_value = mock_client

        provider = OpenAiProvider(api_key="sk-test")
        with pytest.raises(AiProviderConnectionError):
            provider.chat_completion(_base_request())

    @patch("app.layer4_frameworks.providers.openai_provider.httpx.Client")
    def test_timeout_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = httpx.ReadTimeout("Timed out")
        mock_client_cls.return_value = mock_client

        provider = OpenAiProvider(api_key="sk-test")
        with pytest.raises(AiProviderConnectionError):
            provider.chat_completion(_base_request())
