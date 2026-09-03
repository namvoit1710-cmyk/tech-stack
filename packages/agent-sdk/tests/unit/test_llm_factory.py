from unittest.mock import patch


def test_make_openai_service_stores_model_kwargs_and_preserves_defaults():
    with patch(
        "agent_sdk.layer4_frameworks.config.app_config.settings"
    ) as mock_settings:
        mock_settings.LLM_API_KEY = ""
        mock_settings.OPENAI_API_KEY = "sk-from-settings"
        mock_settings.LLM_MODEL = "gpt-4o-mini"
        mock_settings.LLM_TEMPERATURE = 0.01
        mock_settings.LLM_PROVIDER = "openai"
        mock_settings.LLM_TIMEOUT = 30
        mock_settings.LLM_MAX_TOKENS = 2048
        mock_settings.LLM_MAX_RETRIES = 4

        from agent_sdk.layer2_application.interfaces.chat_completion_service import (
            IChatCompletionService,
        )
        from agent_sdk.layer4_frameworks.ai.llm_factory import make_openai_service

        service = make_openai_service(
            model_kwargs={
                "reasoning": {"effort": "medium"},
            },
        )

    assert service.api_key == "sk-from-settings"
    assert isinstance(service, IChatCompletionService)
    assert service.model == "gpt-4o-mini"
    assert service.temperature == 0.01
    assert service.provider == "openai"
    assert service.timeout == 30
    assert service.max_tokens == 2048
    assert service.max_retries == 4
    assert service.model_kwargs == {
        "reasoning": {"effort": "medium"},
    }


def test_make_llm_service_prefers_llm_api_key_over_openai_api_key():
    with patch(
        "agent_sdk.layer4_frameworks.config.app_config.settings"
    ) as mock_settings:
        mock_settings.LLM_API_KEY = "sk-generic-from-settings"
        mock_settings.OPENAI_API_KEY = "sk-openai-from-settings"
        mock_settings.LLM_MODEL = "claude-3-5-sonnet-latest"
        mock_settings.LLM_TEMPERATURE = 0.2
        mock_settings.LLM_PROVIDER = "anthropic"
        mock_settings.LLM_TIMEOUT = 45
        mock_settings.LLM_MAX_TOKENS = 1024
        mock_settings.LLM_MAX_RETRIES = 3
        mock_settings.LLM_MODEL_KWARGS = {}

        from agent_sdk.layer4_frameworks.ai.llm_factory import make_llm_service

        service = make_llm_service()

    assert service.api_key == "sk-generic-from-settings"
    assert service.model == "claude-3-5-sonnet-latest"
    assert service.provider == "anthropic"
    assert service.temperature == 0.2
    assert service.timeout == 45
    assert service.max_tokens == 1024
    assert service.max_retries == 3


def test_make_llm_service_uses_explicit_settings_object():
    from types import SimpleNamespace

    from agent_sdk.layer4_frameworks.ai.llm_factory import make_llm_service

    runtime_settings = SimpleNamespace(
        LLM_USE_LITELLM_PROXY=False,
        LLM_API_KEY="",
        OPENAI_API_KEY="",
        LLM_MODEL="claude-3-5-sonnet-latest",
        LLM_TEMPERATURE=0.2,
        LLM_PROVIDER="anthropic",
        LLM_TIMEOUT=45,
        LLM_MAX_TOKENS=1024,
        LLM_MAX_RETRIES=3,
        LLM_MODEL_KWARGS={},
        LLM_REASONING_EFFORT=None,
        LLM_USAGE_PRICE_TABLE_JSON="{}",
        LLM_USAGE_CURRENCY="USD",
        LLM_USAGE_COST_ENABLED=False,
        LLM_USAGE_LOG_ENABLED=False,
        AGENT_TYPE="reviewer",
        AGENT_ID="agent-123",
    )

    service = make_llm_service(settings=runtime_settings)

    assert service.api_key is None
    assert service.model == "claude-3-5-sonnet-latest"
    assert service.provider == "anthropic"
    assert service.temperature == 0.2
    assert service.timeout == 45
    assert service.max_tokens == 1024
    assert service.max_retries == 3
    assert service.usage_metadata == {
        "agent_type": "reviewer",
        "agent_id": "agent-123",
    }


def test_make_llm_service_litellm_proxy_ignores_provider_api_keys():
    from types import SimpleNamespace

    from agent_sdk.layer4_frameworks.ai.llm_factory import make_llm_service

    runtime_settings = SimpleNamespace(
        LLM_USE_LITELLM_PROXY=True,
        LITELLM_PROXY_URL="http://localhost:4000/v1",
        LITELLM_PROXY_API_KEY="sk-litellm-token",
        LITELLM_PROXY_MODEL="claude-3-5-sonnet-latest",
        LLM_API_KEY="sk-generic-from-settings",
        OPENAI_API_KEY="sk-openai-from-settings",
        LLM_MODEL="gpt-4o-mini",
        LLM_TEMPERATURE=0.2,
        LLM_PROVIDER="anthropic",
        LLM_TIMEOUT=45,
        LLM_MAX_TOKENS=1024,
        LLM_MAX_RETRIES=3,
        LLM_MODEL_KWARGS={},
        LLM_REASONING_EFFORT=None,
        LLM_USAGE_PRICE_TABLE_JSON="{}",
        LLM_USAGE_CURRENCY="USD",
        LLM_USAGE_COST_ENABLED=False,
        LLM_USAGE_LOG_ENABLED=False,
        AGENT_TYPE="reviewer",
        AGENT_ID="agent-123",
    )

    service = make_llm_service(settings=runtime_settings)

    assert service.api_key == "sk-litellm-token"
    assert service.base_url == "http://localhost:4000/v1"
    assert service.provider == "openai"
    assert service.model == "claude-3-5-sonnet-latest"


def test_make_llm_service_litellm_proxy_uses_placeholder_token_and_default_model():
    from types import SimpleNamespace

    from agent_sdk.layer4_frameworks.ai.llm_factory import make_llm_service

    runtime_settings = SimpleNamespace(
        LLM_USE_LITELLM_PROXY=True,
        LITELLM_PROXY_URL="http://localhost:4000/v1",
        LITELLM_PROXY_API_KEY="",
        LITELLM_PROXY_MODEL="",
        LLM_API_KEY="sk-generic-from-settings",
        OPENAI_API_KEY="sk-openai-from-settings",
        LLM_MODEL="",
        LLM_TEMPERATURE=0.2,
        LLM_PROVIDER="anthropic",
        LLM_TIMEOUT=45,
        LLM_MAX_TOKENS=1024,
        LLM_MAX_RETRIES=3,
        LLM_MODEL_KWARGS={},
        LLM_REASONING_EFFORT=None,
        LLM_USAGE_PRICE_TABLE_JSON="{}",
        LLM_USAGE_CURRENCY="USD",
        LLM_USAGE_COST_ENABLED=False,
        LLM_USAGE_LOG_ENABLED=False,
        AGENT_TYPE="reviewer",
        AGENT_ID="agent-123",
    )

    service = make_llm_service(settings=runtime_settings)

    assert service.api_key == "litellm-proxy"
    assert service.model == "default"
    assert service.provider == "openai"
