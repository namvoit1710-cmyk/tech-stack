"""Unit spec — make_llm_service gap-fill (LLM seam factory).

Gap-fill sweep 2026-07-02 (unit-smith). llm_factory.py sat at 86%. The
existing test_llm_factory.py covers the LLM_API_KEY-over-OPENAI happy path and
model_kwargs. Uncovered branches filled here (grounded in source
``agent_sdk/layer4_frameworks/ai/llm_factory.py``):
  - no api_key + non-openai provider -> NO OpenAI fallback, api_key stays None
  - no api_key + openai-looking -> falls back to settings.OPENAI_API_KEY
  - LLM_USAGE_LOG_ENABLED true -> lazily builds a StandardLogger (lines 89-93)
  - _setting_bool int/bool branches (lines 31, 33)

Five case types: happy · edge · invalid input · boundary · failure path.
"""

from unittest.mock import patch

from agent_sdk.layer4_frameworks.ai.llm_factory import (
    _setting_bool,
    _setting_str,
    _strip,
    make_llm_service,
)


def _base_settings(mock_settings):
    """Wire the minimal attribute set make_llm_service reads."""
    mock_settings.LLM_MODEL = "claude-3-5-sonnet-latest"
    mock_settings.LLM_PROVIDER = "anthropic"
    mock_settings.LLM_API_KEY = ""
    mock_settings.OPENAI_API_KEY = "sk-openai-fallback"
    mock_settings.LLM_REASONING_EFFORT = None
    mock_settings.LLM_USAGE_PRICE_TABLE_JSON = "{}"
    mock_settings.LLM_USAGE_CURRENCY = "USD"
    mock_settings.LLM_USAGE_COST_ENABLED = False
    mock_settings.LLM_USAGE_LOG_ENABLED = False
    mock_settings.AGENT_TYPE = "echo"
    mock_settings.AGENT_ID = "agent-1"
    mock_settings.LLM_TEMPERATURE = 0.2
    mock_settings.LLM_TIMEOUT = 30
    mock_settings.LLM_MAX_TOKENS = 1024
    mock_settings.LLM_MAX_RETRIES = 3
    mock_settings.LLM_MODEL_KWARGS = {}
    return mock_settings


# ── happy path: non-openai provider with no key -> NO OpenAI fallback ──────
def test_no_key_non_openai_provider_leaves_api_key_none():
    # Source: OPENAI_API_KEY fallback is gated by looks_like_openai(); anthropic skips it.
    with patch("agent_sdk.layer4_frameworks.config.app_config.settings") as s:
        _base_settings(s)
        service = make_llm_service()
    assert service.api_key is None
    assert service.provider == "anthropic"


# ── edge: openai-looking provider with no key -> OPENAI_API_KEY fallback ───
def test_no_key_openai_provider_falls_back_to_openai_api_key():
    with patch("agent_sdk.layer4_frameworks.config.app_config.settings") as s:
        _base_settings(s)
        s.LLM_PROVIDER = "openai"
        s.LLM_MODEL = "gpt-4o-mini"
        service = make_llm_service()
    assert service.api_key == "sk-openai-fallback"


# ── edge: explicit api_key arg wins over settings ──────────────────────────
def test_explicit_api_key_arg_takes_precedence():
    with patch("agent_sdk.layer4_frameworks.config.app_config.settings") as s:
        _base_settings(s)
        service = make_llm_service(api_key="  sk-explicit  ")  # _strip trims it
    assert service.api_key == "sk-explicit"


# ── failure/behaviour path: usage-log enabled builds a StandardLogger ──────
def test_usage_log_enabled_builds_standard_logger_when_none_given():
    # Source lines 89-93: logger is None + LLM_USAGE_LOG_ENABLED -> StandardLogger().
    from agent_sdk.layer4_frameworks.logging.standard import StandardLogger

    with patch("agent_sdk.layer4_frameworks.config.app_config.settings") as s:
        _base_settings(s)
        s.LLM_USAGE_LOG_ENABLED = True
        with patch.object(StandardLogger, "__init__", return_value=None) as mock_init:
            service = make_llm_service()
            mock_init.assert_called_once()
    assert service is not None


# ── boundary: reasoning_effort explicit arg overrides settings ─────────────
def test_reasoning_effort_arg_is_stripped():
    with patch("agent_sdk.layer4_frameworks.config.app_config.settings") as s:
        _base_settings(s)
        service = make_llm_service(reasoning_effort="  high  ")
    assert service.reasoning_effort == "high"


# ══ helper predicates: _setting_bool / _setting_str / _strip ════════════════
def test_setting_bool_true_variants():
    # Source lines 30-31: str truthy tokens.
    for token in ("1", "true", "YES", "on", " True "):
        assert _setting_bool(token) is True
    for token in ("0", "false", "off", "nope", ""):
        assert _setting_bool(token) is False


def test_setting_bool_int_and_native_bool():
    # Source lines 28-29, 32-33: bool passthrough + int coercion.
    assert _setting_bool(True) is True
    assert _setting_bool(5) is True
    assert _setting_bool(0) is False


def test_setting_bool_unsupported_type_uses_default():
    assert _setting_bool(object(), default=True) is True
    assert _setting_bool(None) is False


def test_setting_str_falls_back_for_non_str():
    assert _setting_str("kept") == "kept"
    assert _setting_str(123, default="fallback") == "fallback"


def test_strip_none_and_blank_return_none():
    assert _strip(None) is None
    assert _strip("   ") is None
    assert _strip("  x ") == "x"
