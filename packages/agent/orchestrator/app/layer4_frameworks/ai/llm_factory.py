"""
LLM provider factory.

Returns the appropriate ILLMProvider implementation based on settings.LLM_PROVIDER.
"""
import logging

logger = logging.getLogger(__name__)


def create_llm_provider(settings):
    provider = getattr(settings, "LLM_PROVIDER", "mock").lower()

    if provider == "openai":
        from app.layer4_frameworks.ai.openai_llm_provider import OpenAILLMProvider

        api_key = getattr(settings, "OPENAI_API_KEY", "")
        model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

        if not api_key:
            logger.warning(
                "LLM_PROVIDER=openai but OPENAI_API_KEY is empty — falling back to mock"
            )
            from app.layer4_frameworks.ai.mock_llm_provider import MockLLMProvider
            return MockLLMProvider()

        logger.info("LLM provider created: OpenAI (model=%s)", model)
        return OpenAILLMProvider(api_key=api_key, model=model)

    from app.layer4_frameworks.ai.mock_llm_provider import MockLLMProvider
    logger.info("LLM provider created: mock")
    return MockLLMProvider()
