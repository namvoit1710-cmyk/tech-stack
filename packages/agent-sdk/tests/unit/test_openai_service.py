"""Unit tests for SDK-owned generic chat model service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


def _make_service(
    api_key: str = "test-key", model: str = "gpt-4o-mini", temperature: float = 0.01
):
    """Helper to build OpenAIService with explicit overrides (no real clients)."""
    from agent_sdk.layer4_frameworks.ai.openai_service import OpenAIService

    return OpenAIService(api_key=api_key, model=model, temperature=temperature)


def test_openai_service_stores_model():
    svc = _make_service(model="gpt-4o")
    assert svc.model == "gpt-4o"


def test_openai_service_stores_temperature():
    svc = _make_service(temperature=0.5)
    assert svc.temperature == 0.5


def test_openai_service_stores_api_key():
    svc = _make_service(api_key="sk-test")
    assert svc.api_key == "sk-test"


def test_openai_service_stores_provider():
    from agent_sdk.layer4_frameworks.ai.openai_service import OpenAIService

    svc = OpenAIService(
        api_key="sk-test", model="gpt-4o-mini", temperature=0.01, provider="openai"
    )
    assert svc.provider == "openai"


def test_openai_service_async_client_lazy():
    """The chat client should be lazily created (not instantiated at construction time)."""
    with patch(
        "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model"
    ) as mock_init:
        _make_service()
        mock_init.assert_not_called()


def test_openai_service_exposes_chat_model_and_completion_helper():
    """OpenAIService should expose both the chat client and compatibility helper."""
    from agent_sdk.layer2_application.interfaces.chat_completion_service import (
        IChatCompletionService,
    )
    from agent_sdk.layer2_application.interfaces.llm_service import ILLMService
    from agent_sdk.layer4_frameworks.ai.openai_service import OpenAIService

    svc = OpenAIService(api_key="sk-test", model="gpt-4o-mini", temperature=0.01)
    assert hasattr(svc, "get_chat_client")
    assert hasattr(svc, "get_chat_completion")
    assert ILLMService in OpenAIService.__mro__
    assert IChatCompletionService in OpenAIService.__mro__


@pytest.mark.asyncio
async def test_get_chat_completion_supports_keyword_arguments_and_json_mode_by_default():
    svc = _make_service(api_key="sk-test")
    bound_client = MagicMock(name="json_bound_client")
    bound_client.ainvoke = AsyncMock(return_value=AIMessage(content='{"ok": true}'))
    client = MagicMock(name="chat_client")
    client.bind = MagicMock(return_value=bound_client)
    svc.get_chat_client = MagicMock(return_value=client)

    result = await svc.get_chat_completion(
        system_prompt="system instructions",
        user_prompt="user request",
    )

    svc.get_chat_client.assert_called_once_with()
    client.bind.assert_called_once_with(response_format={"type": "json_object"})
    bound_client.ainvoke.assert_awaited_once()
    messages = bound_client.ainvoke.await_args.args[0]
    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content == (
        "system instructions\n\nReturn only valid JSON. Do not include markdown fences, comments, or extra prose."
    )
    assert isinstance(messages[1], HumanMessage)
    assert messages[1].content == "user request"
    assert result == '{"ok": true}'


@pytest.mark.asyncio
async def test_get_chat_completion_supports_positional_arguments():
    svc = _make_service(api_key="sk-test")
    bound_client = MagicMock(name="json_bound_client")
    bound_client.ainvoke = AsyncMock(return_value=AIMessage(content="positional"))
    client = MagicMock(name="chat_client")
    client.bind = MagicMock(return_value=bound_client)
    svc.get_chat_client = MagicMock(return_value=client)

    result = await svc.get_chat_completion("system prompt", "user prompt")

    messages = bound_client.ainvoke.await_args.args[0]
    assert messages[0].content == (
        "system prompt\n\nReturn only valid JSON. Do not include markdown fences, comments, or extra prose."
    )
    assert messages[1].content == "user prompt"
    assert result == "positional"


@pytest.mark.asyncio
async def test_get_chat_completion_skips_json_binding_when_json_mode_disabled():
    svc = _make_service(api_key="sk-test")
    client = MagicMock(name="chat_client")
    client.ainvoke = AsyncMock(return_value=AIMessage(content="plain text"))
    client.bind = MagicMock()
    svc.get_chat_client = MagicMock(return_value=client)

    result = await svc.get_chat_completion(
        system_prompt="system instructions",
        user_prompt="user request",
        json_mode=False,
    )

    client.bind.assert_not_called()
    client.ainvoke.assert_awaited_once()
    assert result == "plain text"


@pytest.mark.asyncio
async def test_get_chat_completion_returns_raw_string_content():
    svc = _make_service(api_key="sk-test")
    client = MagicMock(name="chat_client")
    client.ainvoke = AsyncMock(
        return_value=AIMessage(content=[{"type": "text", "text": "hi"}])
    )
    svc.get_chat_client = MagicMock(return_value=client)

    result = await svc.get_chat_completion(
        system_prompt="system instructions",
        user_prompt="user request",
        json_mode=False,
    )

    assert result == "hi"


def test_get_chat_client_returns_initialized_chat_model():
    """get_chat_client should return the provider-agnostic init_chat_model instance."""
    with patch(
        "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model"
    ) as mock_init:
        mock_instance = MagicMock()
        mock_init.return_value = mock_instance

        svc = _make_service(api_key="sk-test")
        client = svc.get_chat_client()

        mock_init.assert_called_once_with(
            "gpt-4o-mini",
            model_provider=None,
            api_key="sk-test",
            temperature=0.01,
            timeout=None,
            max_tokens=None,
            max_retries=6,
        )
        assert client is mock_instance


def test_get_chat_client_passes_provider_when_configured():
    with patch(
        "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model"
    ) as mock_init:
        mock_init.return_value = MagicMock()

        from agent_sdk.layer4_frameworks.ai.openai_service import OpenAIService

        svc = OpenAIService(
            api_key="sk-test",
            model="claude-3-5-sonnet-latest",
            temperature=0.2,
            provider="anthropic",
        )
        svc.get_chat_client()

        mock_init.assert_called_once_with(
            "claude-3-5-sonnet-latest",
            model_provider="anthropic",
            api_key="sk-test",
            temperature=0.2,
            timeout=None,
            max_tokens=None,
            max_retries=6,
        )


def test_get_chat_client_passes_provider_specific_model_kwargs_through():
    with patch(
        "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model"
    ) as mock_init:
        mock_init.return_value = MagicMock()

        from agent_sdk.layer4_frameworks.ai.openai_service import OpenAIService

        svc = OpenAIService(
            api_key="sk-test",
            model="gpt-4o-mini",
            temperature=0.01,
            model_kwargs={
                "reasoning": {"effort": "medium"},
            },
        )
        svc.get_chat_client()

        mock_init.assert_called_once_with(
            "gpt-4o-mini",
            model_provider=None,
            api_key="sk-test",
            temperature=0.01,
            timeout=None,
            max_tokens=None,
            max_retries=6,
        )


def test_get_chat_client_prefers_named_args_over_duplicate_model_kwargs_keys():
    with patch(
        "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model"
    ) as mock_init:
        mock_init.return_value = MagicMock()

        from agent_sdk.layer4_frameworks.ai.openai_service import OpenAIService

        svc = OpenAIService(
            api_key="sk-test",
            model="gpt-4o-mini",
            temperature=0.2,
            model_kwargs={
                "temperature": 0.9,
                "reasoning": {"effort": "medium"},
            },
        )
        svc.get_chat_client()

        mock_init.assert_called_once_with(
            "gpt-4o-mini",
            model_provider=None,
            api_key="sk-test",
            temperature=0.2,
            timeout=None,
            max_tokens=None,
            max_retries=6,
        )


def test_get_chat_client_returns_singleton():
    """get_chat_client should return the same initialized chat model on repeated calls."""
    with patch(
        "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model"
    ) as mock_init:
        mock_instance = MagicMock()
        mock_init.return_value = mock_instance

        svc = _make_service(api_key="sk-test")
        client1 = svc.get_chat_client()
        client2 = svc.get_chat_client()

        assert mock_init.call_count == 1
        assert client1 is client2


def test_openai_service_omits_api_key_when_not_provided():
    """OpenAIService should allow provider env vars to supply credentials implicitly."""
    from agent_sdk.layer4_frameworks.ai.openai_service import OpenAIService

    with patch(
        "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model"
    ) as mock_init:
        mock_init.return_value = MagicMock()

        OpenAIService(api_key="", model="gpt-4o-mini").get_chat_client()

    _, kwargs = mock_init.call_args
    assert "api_key" not in kwargs


def test_openai_service_does_not_store_raw_openai_client_state():
    """Single-path cleanup should remove the separate raw OpenAI client cache."""
    svc = _make_service(api_key="sk-test")
    assert not hasattr(svc, "_async_client")


def test_openai_service_uses_built_in_defaults_and_ignores_settings():
    from agent_sdk.layer4_frameworks.ai.openai_service import OpenAIService

    with patch(
        "agent_sdk.layer4_frameworks.config.app_config.settings"
    ) as mock_settings:
        mock_settings.LLM_MODEL = "claude-3-5-sonnet-latest"
        mock_settings.LLM_PROVIDER = "anthropic"
        mock_settings.LLM_TEMPERATURE = 0.3
        mock_settings.LLM_TIMEOUT = 15
        mock_settings.LLM_MAX_TOKENS = 512
        mock_settings.LLM_MAX_RETRIES = 4
        mock_settings.OPENAI_API_KEY = "sk-from-settings"

        svc = OpenAIService()

    assert svc.api_key is None
    assert svc.model == "gpt-4o-mini"
    assert svc.provider is None
    assert svc.temperature == 0.01
    assert svc.timeout is None
    assert svc.max_tokens is None
    assert svc.max_retries == 6
    assert svc.model_kwargs == {}
