"""Tests for SDK auto-wiring of the single supported LLM dependency path."""

from unittest.mock import MagicMock, patch


def _configure_generic_llm_settings(mock_settings):
    mock_settings.WORKER_EXECUTOR_URL = ""
    mock_settings.DEFAULT_TENANT_ID = "default"
    mock_settings.APP_MODE = "SERVER"
    mock_settings.LLM_API_KEY = ""
    mock_settings.OPENAI_API_KEY = ""
    mock_settings.LLM_MODEL = "gpt-4o-mini"
    mock_settings.LLM_TEMPERATURE = 0.01
    mock_settings.LLM_PROVIDER = ""
    mock_settings.LLM_TIMEOUT = None
    mock_settings.LLM_MAX_TOKENS = None
    mock_settings.LLM_MAX_RETRIES = 6
    mock_settings.MCP_SERVERS = []
    mock_settings.MCP_TOOL_FILTER = []


@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_exposes_openai_service_alias_when_generic_llm_configured(
    mock_settings,
):
    """build_app_container should keep the compatibility openai_service alias while wiring llm."""
    _configure_generic_llm_settings(mock_settings)

    with patch("agent_sdk.layer4_frameworks.config.app_config.settings", mock_settings):
        with patch(
            "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model"
        ) as mock_init_chat_model:
            mock_init_chat_model.return_value = MagicMock(name="llm")

            from agent_sdk.bootstrap import build_app_container

            container = build_app_container()
    deps = container["_dependencies"]
    assert deps["llm"] is mock_init_chat_model.return_value
    assert deps["openai_service"] is deps["llm_service"]
    assert deps["chat_completion_service"] is deps["llm_service"]


@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_injects_llm_when_generic_llm_configured(mock_settings):
    """build_app_container must add llm when generic LLM config is present."""
    _configure_generic_llm_settings(mock_settings)

    with patch("agent_sdk.layer4_frameworks.config.app_config.settings", mock_settings):
        with patch(
            "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model"
        ) as mock_init_chat_model:
            mock_llm = MagicMock(name="llm")
            mock_init_chat_model.return_value = mock_llm

            from agent_sdk.bootstrap import build_app_container

            container = build_app_container()
    deps = container["_dependencies"]
    assert (
        "llm" in deps
    ), "build_app_container must inject llm when generic LLM config is configured"
    assert deps["llm"] is mock_llm


@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_uses_init_chat_model_with_generic_settings(mock_settings):
    _configure_generic_llm_settings(mock_settings)
    mock_settings.LLM_PROVIDER = "anthropic"
    mock_settings.LLM_MODEL = "claude-3-5-sonnet-latest"
    mock_settings.LLM_TEMPERATURE = 0.2
    mock_settings.LLM_TIMEOUT = 30
    mock_settings.LLM_MAX_TOKENS = 1024
    mock_settings.LLM_MAX_RETRIES = 4

    with patch("agent_sdk.layer4_frameworks.config.app_config.settings", mock_settings):
        with patch(
            "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model"
        ) as mock_init_chat_model:
            mock_init_chat_model.return_value = MagicMock(name="llm")

            from agent_sdk.bootstrap import build_app_container

            build_app_container()

    mock_init_chat_model.assert_called_once_with(
        "claude-3-5-sonnet-latest",
        model_provider="anthropic",
        temperature=0.2,
        timeout=30,
        max_tokens=1024,
        max_retries=4,
    )


@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_passes_model_kwargs_from_settings_to_llm_service(mock_settings):
    _configure_generic_llm_settings(mock_settings)
    mock_settings.LLM_MODEL_KWARGS = {
        "reasoning": {"effort": "medium"},
    }

    with patch("agent_sdk.layer4_frameworks.config.app_config.settings", mock_settings):
        with patch(
            "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model"
        ) as mock_init_chat_model:
            mock_init_chat_model.return_value = MagicMock(name="llm")

            from agent_sdk.bootstrap import build_app_container

            build_app_container()

    mock_init_chat_model.assert_called_once_with(
        "gpt-4o-mini",
        model_provider=None,
        temperature=0.01,
        timeout=None,
        max_tokens=None,
        max_retries=6,
    )


@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_passes_openai_api_key_from_settings_to_llm_service(
    mock_settings,
):
    _configure_generic_llm_settings(mock_settings)
    mock_settings.OPENAI_API_KEY = "sk-from-settings"

    with patch("agent_sdk.layer4_frameworks.config.app_config.settings", mock_settings):
        with patch(
            "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model"
        ) as mock_init_chat_model:
            mock_init_chat_model.return_value = MagicMock(name="llm")

            from agent_sdk.bootstrap import build_app_container

            build_app_container()

    mock_init_chat_model.assert_called_once_with(
        "gpt-4o-mini",
        model_provider=None,
        temperature=0.01,
        timeout=None,
        max_tokens=None,
        max_retries=6,
        api_key="sk-from-settings",
    )


@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_does_not_inject_llm_when_generic_model_empty(mock_settings):
    """build_app_container must NOT inject llm when generic LLM model is empty."""
    _configure_generic_llm_settings(mock_settings)
    mock_settings.LLM_MODEL = ""

    from agent_sdk.bootstrap import build_app_container

    container = build_app_container()
    deps = container["_dependencies"]
    assert (
        "llm" not in deps
    ), "build_app_container must NOT inject llm when generic LLM model is empty"


@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_caller_supplied_openai_service_is_preserved_while_llm_is_derived(
    mock_settings,
):
    """A caller-supplied openai_service should be preserved as the compatibility alias while llm is derived from it."""
    _configure_generic_llm_settings(mock_settings)

    custom_service = MagicMock(name="custom_openai_service")
    custom_llm = MagicMock(name="custom_llm_from_service")
    custom_service.get_chat_client.return_value = custom_llm

    from agent_sdk.bootstrap import build_app_container

    container = build_app_container(
        extra_dependencies={"openai_service": custom_service}
    )
    deps = container["_dependencies"]
    assert deps["llm"] is custom_llm
    assert deps["openai_service"] is custom_service
    assert deps["chat_completion_service"] is custom_service


@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_caller_supplied_llm_wins(mock_settings):
    """extra_dependencies={'llm': custom_llm} must override SDK default (override precedence)."""
    _configure_generic_llm_settings(mock_settings)

    custom_llm = MagicMock(name="custom_llm")

    from agent_sdk.bootstrap import build_app_container

    container = build_app_container(extra_dependencies={"llm": custom_llm})
    deps = container["_dependencies"]
    assert (
        deps["llm"] is custom_llm
    ), "Caller-supplied llm in extra_dependencies must override SDK default"

@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_dependency_override_openai_service_is_preserved_while_llm_is_derived(
    mock_settings,
):
    """dependency_overrides openai_service must behave like a caller-supplied compatibility alias."""
    _configure_generic_llm_settings(mock_settings)

    custom_service = MagicMock(name="custom_openai_service")
    custom_llm = MagicMock(name="custom_llm_from_service")
    custom_service.get_chat_client.return_value = custom_llm

    from agent_sdk.bootstrap import build_app_container

    container = build_app_container(
        dependency_overrides={"openai_service": custom_service}
    )
    deps = container["_dependencies"]
    assert deps["llm"] is custom_llm
    assert deps["openai_service"] is custom_service
    assert deps["chat_completion_service"] is custom_service


def test_bootstrap_uses_dependency_override_settings_for_llm_factory():
    from types import SimpleNamespace

    runtime_settings = SimpleNamespace(
        DEFAULT_TENANT_ID="default",
        APP_MODE="SERVER",
        LLM_API_KEY="",
        OPENAI_API_KEY="",
        LLM_MODEL="claude-3-5-sonnet-latest",
        LLM_TEMPERATURE=0.2,
        LLM_PROVIDER="anthropic",
        LLM_TIMEOUT=30,
        LLM_MAX_TOKENS=1024,
        LLM_MAX_RETRIES=4,
        LLM_MODEL_KWARGS={},
        MCP_SERVERS=[],
        MCP_TOOL_FILTER=[],
        AGENT_TYPE="test-agent",
        AGENT_ID="agent-123",
    )

    with patch(
        "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model"
    ) as mock_init_chat_model:
        mock_init_chat_model.return_value = MagicMock(name="llm")

        from agent_sdk.bootstrap import build_app_container

        build_app_container(dependency_overrides={"settings": runtime_settings})

    mock_init_chat_model.assert_called_once_with(
        "claude-3-5-sonnet-latest",
        model_provider="anthropic",
        temperature=0.2,
        timeout=30,
        max_tokens=1024,
        max_retries=4,
    )
