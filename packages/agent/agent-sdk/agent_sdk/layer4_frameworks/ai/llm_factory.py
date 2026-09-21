from __future__ import annotations

from typing import Any, Optional

from agent_sdk.layer2_application.interfaces.chat_completion_service import (
    IChatCompletionService,
)
from agent_sdk.layer2_application.interfaces.observability import ILogger, IMonitor
from agent_sdk.layer2_application.services.llm_cost_calculator import LLMCostCalculator
from agent_sdk.layer2_application.services.llm_usage_recorder import (
    LoggingLLMUsageRecorder,
)
from agent_sdk.layer4_frameworks.ai.openai_service import (
    LangChainLLMService,
    OpenAIService,
    looks_like_openai,
)


def _strip(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _setting_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, int):
        return bool(value)
    return default


def _setting_str(value: Any, *, default: str = "") -> str:
    return value if isinstance(value, str) else default


def make_llm_service(
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    provider: Optional[str] = None,
    timeout: Optional[float] = None,
    max_tokens: Optional[int] = None,
    max_retries: Optional[int] = None,
    model_kwargs: Optional[dict[str, Any]] = None,
    reasoning_effort: Optional[str] = None,
    logger: ILogger | None = None,
    monitor: IMonitor | None = None,
    agent_type: Optional[str] = None,
    agent_id: Optional[str] = None,
    settings: Any | None = None,
) -> IChatCompletionService:
    from agent_sdk.layer4_frameworks.config.app_config import (
        settings as default_settings,
    )

    runtime_settings = default_settings if settings is None else settings

    use_litellm_proxy = _setting_bool(
        getattr(runtime_settings, "LLM_USE_LITELLM_PROXY", False),
        default=False,
    )
    resolved_model = _strip(model) or _strip(getattr(runtime_settings, "LLM_MODEL", ""))
    resolved_provider = (
        provider if provider is not None else runtime_settings.LLM_PROVIDER
    )
    resolved_api_key = _strip(api_key)
    resolved_base_url: str | None = None

    if use_litellm_proxy:
        resolved_base_url = _strip(getattr(runtime_settings, "LITELLM_PROXY_URL", ""))
        if resolved_base_url is None:
            raise ValueError(
                "LLM_USE_LITELLM_PROXY=true requires LITELLM_PROXY_URL to be set"
            )
        resolved_provider = "openai"
        resolved_model = (
            _strip(model)
            or _strip(getattr(runtime_settings, "LITELLM_PROXY_MODEL", ""))
            or "default"
        )

    if resolved_api_key is None:
        if use_litellm_proxy:
            resolved_api_key = (
                _strip(getattr(runtime_settings, "LITELLM_PROXY_API_KEY", ""))
                or "litellm-proxy"
            )
        else:
            resolved_api_key = _strip(getattr(runtime_settings, "LLM_API_KEY", ""))
            if resolved_api_key is None and looks_like_openai(
                resolved_provider, resolved_model
            ):
                resolved_api_key = _strip(
                    getattr(runtime_settings, "OPENAI_API_KEY", "")
                )

    resolved_reasoning_effort = (
        _strip(reasoning_effort)
        if reasoning_effort is not None
        else _strip(getattr(runtime_settings, "LLM_REASONING_EFFORT", None))
    )

    usage_recorder = None
    usage_cost_calculator = LLMCostCalculator.from_json(
        _setting_str(
            getattr(runtime_settings, "LLM_USAGE_PRICE_TABLE_JSON", "{}"),
            default="{}",
        ),
        currency=_setting_str(
            getattr(runtime_settings, "LLM_USAGE_CURRENCY", "USD"), default="USD"
        ),
        enabled=_setting_bool(
            getattr(runtime_settings, "LLM_USAGE_COST_ENABLED", False),
            default=False,
        ),
    )
    if _setting_bool(
        getattr(runtime_settings, "LLM_USAGE_LOG_ENABLED", False),
        default=False,
    ):
        if logger is None:
            from agent_sdk.layer4_frameworks.logging.standard import StandardLogger

            logger = StandardLogger()
        usage_recorder = LoggingLLMUsageRecorder(
            logger=logger,
            monitor=monitor,
            enabled=True,
        )

    usage_metadata = {
        "agent_type": agent_type
        if agent_type is not None
        else runtime_settings.AGENT_TYPE,
        "agent_id": agent_id
        if agent_id is not None
        else getattr(runtime_settings, "AGENT_ID", ""),
    }

    return LangChainLLMService(
        api_key=resolved_api_key,
        model=resolved_model,
        temperature=temperature
        if temperature is not None
        else runtime_settings.LLM_TEMPERATURE,
        provider=resolved_provider,
        base_url=resolved_base_url,
        timeout=timeout if timeout is not None else runtime_settings.LLM_TIMEOUT,
        max_tokens=max_tokens
        if max_tokens is not None
        else runtime_settings.LLM_MAX_TOKENS,
        max_retries=max_retries
        if max_retries is not None
        else runtime_settings.LLM_MAX_RETRIES,
        model_kwargs=(
            model_kwargs
            if model_kwargs is not None
            else runtime_settings.LLM_MODEL_KWARGS
        ),
        reasoning_effort=resolved_reasoning_effort,
        usage_recorder=usage_recorder,
        usage_cost_calculator=usage_cost_calculator,
        usage_metadata=usage_metadata,
    )


def make_openai_service(
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    model_kwargs: Optional[dict[str, Any]] = None,
    reasoning_effort: Optional[str] = None,
    logger: ILogger | None = None,
    monitor: IMonitor | None = None,
    agent_type: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> IChatCompletionService:
    """Backward-compatible factory.

    Existing examples can keep calling this. New provider-agnostic code should
    call ``make_llm_service``.
    """

    return make_llm_service(
        api_key=api_key,
        model=model,
        temperature=temperature,
        model_kwargs=model_kwargs,
        reasoning_effort=reasoning_effort,
        logger=logger,
        monitor=monitor,
        agent_type=agent_type,
        agent_id=agent_id,
    )


__all__ = [
    "LangChainLLMService",
    "OpenAIService",
    "make_llm_service",
    "make_openai_service",
]
