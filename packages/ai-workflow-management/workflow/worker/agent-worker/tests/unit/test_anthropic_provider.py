"""Unit specs for the Anthropic provider (AnthropicProvider).

Authored by unit-smith (white-box). Fills the 0%-covered
app/layer4_frameworks/providers/anthropic_provider.py module. Mocks the layer
below the unit — httpx.Client — at its real seam
(app.layer4_frameworks.providers.anthropic_provider.httpx.Client), mirroring the
sibling test_providers.py OpenAI style exactly.

Every expected value/exception is grounded in the source:
- 401 -> AiProviderAuthenticationError   (source line 96-97)
- 429 -> AiProviderRateLimitError        (source line 98-99)
- >=400 -> AiProviderResponseError        (source line 100-104)
- httpx.ConnectError / TimeoutException -> AiProviderConnectionError (source line 60-63)
- system message is lifted into payload['system'], not messages (source line 40-44, 54-55)
- usage: input_tokens->prompt, output_tokens->completion, total=sum (source line 72-83)
"""

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
from app.layer4_frameworks.providers.anthropic_provider import AnthropicProvider


def _base_request(
    messages=None,
    model: str = "claude-3-5-sonnet",
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


def _wire_client(mock_client_cls, *, post_return=None, post_side_effect=None) -> MagicMock:
    """Wire the patched httpx.Client class to a context-manager mock client."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    if post_side_effect is not None:
        mock_client.post.side_effect = post_side_effect
    else:
        mock_client.post.return_value = post_return
    mock_client_cls.return_value = mock_client
    return mock_client


_OK_BODY = {
    "content": [{"type": "text", "text": "Hi there!"}],
    "model": "claude-3-5-sonnet",
    "usage": {"input_tokens": 5, "output_tokens": 3},
    "stop_reason": "end_turn",
}


class TestAnthropicHappyPath:
    # happy path
    @patch("app.layer4_frameworks.providers.anthropic_provider.httpx.Client")
    def test_successful_completion(self, mock_client_cls):
        _wire_client(mock_client_cls, post_return=_mock_httpx_response(200, _OK_BODY))

        provider = AnthropicProvider(api_key="sk-ant-test")
        result = provider.chat_completion(_base_request())

        assert result.content == "Hi there!"
        assert result.model == "claude-3-5-sonnet"
        # stop_reason maps straight to finish_reason (source line 83)
        assert result.finish_reason == "end_turn"
        # input_tokens/output_tokens map to prompt/completion; total is their sum
        assert result.usage.prompt_tokens == 5
        assert result.usage.completion_tokens == 3
        assert result.usage.total_tokens == 8

    # happy path — headers + endpoint are what the source builds
    @patch("app.layer4_frameworks.providers.anthropic_provider.httpx.Client")
    def test_request_headers_and_url(self, mock_client_cls):
        client = _wire_client(mock_client_cls, post_return=_mock_httpx_response(200, _OK_BODY))

        provider = AnthropicProvider(api_key="sk-ant-key")
        provider.chat_completion(_base_request())

        args, kwargs = client.post.call_args
        assert args[0] == "https://api.anthropic.com/v1/messages"
        headers = kwargs["headers"]
        assert headers["x-api-key"] == "sk-ant-key"
        assert headers["anthropic-version"] == "2023-06-01"
        assert headers["Content-Type"] == "application/json"

    # happy path — system message is split out of messages into payload['system']
    @patch("app.layer4_frameworks.providers.anthropic_provider.httpx.Client")
    def test_system_message_lifted_to_system_param(self, mock_client_cls):
        client = _wire_client(mock_client_cls, post_return=_mock_httpx_response(200, _OK_BODY))

        provider = AnthropicProvider(api_key="sk-ant-test")
        provider.chat_completion(_base_request(messages=[
            AiMessage(role=AiRole.SYSTEM, content="You are a pirate."),
            AiMessage(role=AiRole.USER, content="Greet me."),
        ]))

        payload = client.post.call_args.kwargs["json"]
        assert payload["system"] == "You are a pirate."
        # system is NOT in the messages array — only the user turn is
        assert payload["messages"] == [{"role": "user", "content": "Greet me."}]
        assert payload["model"] == "claude-3-5-sonnet"
        assert payload["max_tokens"] == 1024
        assert payload["temperature"] == 0.7
        assert payload["top_p"] == 1.0


class TestAnthropicBoundary:
    # boundary — no system message => payload has no 'system' key (source line 54-55 guard)
    @patch("app.layer4_frameworks.providers.anthropic_provider.httpx.Client")
    def test_no_system_message_omits_system_key(self, mock_client_cls):
        client = _wire_client(mock_client_cls, post_return=_mock_httpx_response(200, _OK_BODY))

        provider = AnthropicProvider(api_key="sk-ant-test")
        provider.chat_completion(_base_request())

        payload = client.post.call_args.kwargs["json"]
        assert "system" not in payload

    # boundary — empty content blocks => empty string, not an error
    @patch("app.layer4_frameworks.providers.anthropic_provider.httpx.Client")
    def test_empty_content_blocks(self, mock_client_cls):
        body = {"content": [], "model": "claude-3-5-sonnet", "usage": {}, "stop_reason": "end_turn"}
        _wire_client(mock_client_cls, post_return=_mock_httpx_response(200, body))

        provider = AnthropicProvider(api_key="sk-ant-test")
        result = provider.chat_completion(_base_request())

        assert result.content == ""
        # missing usage keys default to 0 (source line 72-73)
        assert result.usage.prompt_tokens == 0
        assert result.usage.completion_tokens == 0
        assert result.usage.total_tokens == 0

    # edge — multiple text blocks are concatenated; non-text blocks are skipped
    @patch("app.layer4_frameworks.providers.anthropic_provider.httpx.Client")
    def test_multiple_text_blocks_concatenated(self, mock_client_cls):
        body = {
            "content": [
                {"type": "text", "text": "Hello "},
                {"type": "image", "source": "ignored"},
                {"type": "text", "text": "world"},
            ],
            "model": "claude-3-5-sonnet",
            "usage": {"input_tokens": 1, "output_tokens": 2},
            "stop_reason": "end_turn",
        }
        _wire_client(mock_client_cls, post_return=_mock_httpx_response(200, body))

        provider = AnthropicProvider(api_key="sk-ant-test")
        result = provider.chat_completion(_base_request())

        assert result.content == "Hello world"

    # edge — model missing from response falls back to the request model (source line 77)
    @patch("app.layer4_frameworks.providers.anthropic_provider.httpx.Client")
    def test_missing_model_falls_back_to_request(self, mock_client_cls):
        body = {"content": [{"type": "text", "text": "x"}], "usage": {}, "stop_reason": "end_turn"}
        _wire_client(mock_client_cls, post_return=_mock_httpx_response(200, body))

        provider = AnthropicProvider(api_key="sk-ant-test")
        result = provider.chat_completion(_base_request(model="claude-opus-req"))

        assert result.model == "claude-opus-req"

    # edge — missing stop_reason defaults to "unknown" (source line 83)
    @patch("app.layer4_frameworks.providers.anthropic_provider.httpx.Client")
    def test_missing_stop_reason_defaults_unknown(self, mock_client_cls):
        body = {"content": [{"type": "text", "text": "x"}], "model": "m", "usage": {}}
        _wire_client(mock_client_cls, post_return=_mock_httpx_response(200, body))

        provider = AnthropicProvider(api_key="sk-ant-test")
        result = provider.chat_completion(_base_request())

        assert result.finish_reason == "unknown"


class TestAnthropicFailurePaths:
    # failure path — 401
    @patch("app.layer4_frameworks.providers.anthropic_provider.httpx.Client")
    def test_auth_error(self, mock_client_cls):
        _wire_client(mock_client_cls, post_return=_mock_httpx_response(401, text="Unauthorized"))

        provider = AnthropicProvider(api_key="bad-key")
        with pytest.raises(AiProviderAuthenticationError):
            provider.chat_completion(_base_request())

    # failure path — 429
    @patch("app.layer4_frameworks.providers.anthropic_provider.httpx.Client")
    def test_rate_limit_error(self, mock_client_cls):
        _wire_client(mock_client_cls, post_return=_mock_httpx_response(429, text="Slow down"))

        provider = AnthropicProvider(api_key="sk-ant-test")
        with pytest.raises(AiProviderRateLimitError):
            provider.chat_completion(_base_request())

    # failure path — 500 (>=400 catch-all) carries the status code in the message
    @patch("app.layer4_frameworks.providers.anthropic_provider.httpx.Client")
    def test_server_error(self, mock_client_cls):
        _wire_client(mock_client_cls, post_return=_mock_httpx_response(500, text="boom"))

        provider = AnthropicProvider(api_key="sk-ant-test")
        with pytest.raises(AiProviderResponseError, match="500"):
            provider.chat_completion(_base_request())

    # boundary — 400 is the lowest code that hits the >=400 branch (not 401/429)
    @patch("app.layer4_frameworks.providers.anthropic_provider.httpx.Client")
    def test_bad_request_400_is_response_error(self, mock_client_cls):
        _wire_client(mock_client_cls, post_return=_mock_httpx_response(400, text="bad request"))

        provider = AnthropicProvider(api_key="sk-ant-test")
        with pytest.raises(AiProviderResponseError, match="400"):
            provider.chat_completion(_base_request())

    # failure path — connect error is remapped to AiProviderConnectionError
    @patch("app.layer4_frameworks.providers.anthropic_provider.httpx.Client")
    def test_connection_error(self, mock_client_cls):
        _wire_client(mock_client_cls, post_side_effect=httpx.ConnectError("refused"))

        provider = AnthropicProvider(api_key="sk-ant-test")
        with pytest.raises(AiProviderConnectionError):
            provider.chat_completion(_base_request())

    # failure path — timeout (a TimeoutException subclass) is remapped to ConnectionError
    @patch("app.layer4_frameworks.providers.anthropic_provider.httpx.Client")
    def test_timeout_error(self, mock_client_cls):
        _wire_client(mock_client_cls, post_side_effect=httpx.ReadTimeout("timed out"))

        provider = AnthropicProvider(api_key="sk-ant-test")
        with pytest.raises(AiProviderConnectionError):
            provider.chat_completion(_base_request())
