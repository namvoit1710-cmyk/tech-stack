from typing import Any, Optional

from agent_sdk.layer1_domain.entities.context_config import (
    CompactionStrategy,
    ContextBudgetConfig,
)
from agent_sdk.layer4_frameworks.ai.context_budget import ContextBudgetManager
from agent_sdk.layer4_frameworks.ai.llm_factory import make_llm_service

from app.layer4_frameworks.config.app_config import settings

# Infrastructure master factory
from app.layer4_frameworks import create_infrastructure

# Domain services
from app.layer1_domain.entities.guardrail.input_guard import InputGuard
from app.layer1_domain.entities.guardrail.output_guard import OutputGuard
from app.layer1_domain.entities.classifier.intent_analyzer import IntentClassifier
from app.layer1_domain.entities.classifier.agent_selector import AgentSelector
from app.layer1_domain.entities.orchestrator.response_formatter import ResponseFormatter
from agent_sdk.layer4_frameworks.logging.standard import StandardLogger

from app.layer4_frameworks.base.uuid_builder import UuidBuilder
from app.layer4_frameworks.repositories.in_memory_conversation_run_index import (
    InMemoryConversationRunIndex,
)




def _build_context_budget_manager() -> Optional[ContextBudgetManager]:
    """SA-1948: build the conversation-history compactor from ENV, or None when disabled.

    CONTEXT_COMPACTION_STRATEGY="none" (the single toggle) → returns None so the
    orchestrate use case treats compaction as a no-op. Otherwise maps the 6 CONTEXT_*
    settings onto ContextBudgetConfig and reuses the agent-sdk engine, wiring the
    orchestrator LLM service for TIERED summarization."""
    strategy_value: str = str(settings.CONTEXT_COMPACTION_STRATEGY or "").strip().lower()
    try:
        strategy = CompactionStrategy(strategy_value)
    except ValueError:
        strategy = CompactionStrategy.TIERED
    if strategy == CompactionStrategy.NONE:
        return None

    config = ContextBudgetConfig(
        max_total_tokens=settings.CONTEXT_MAX_TOTAL_TOKENS,
        max_message_history=settings.CONTEXT_MAX_MESSAGE_HISTORY,
        compaction_strategy=strategy,
        compaction_threshold=settings.CONTEXT_COMPACTION_THRESHOLD,
        aggressive_threshold=settings.CONTEXT_AGGRESSIVE_THRESHOLD,
        danger_threshold=settings.CONTEXT_DANGER_THRESHOLD,
    )
    # Token-count model is a DEDICATED setting (CONTEXT_TOKEN_COUNT_MODEL, default
    # gpt-4o-mini), decoupled from LLM_MODEL: tiktoken only needs a known model name to
    # estimate tokens, while the summarize LLM (make_llm_service, via LLM_MODEL) may be any
    # model (e.g. gpt-5) that tiktoken doesn't recognize. Keeping them separate stops an
    # exotic LLM_MODEL from breaking token counting.
    token_model: str = settings.CONTEXT_TOKEN_COUNT_MODEL
    return ContextBudgetManager(
        config,
        model_name=token_model,
        llm_service=make_llm_service(agent_type="orchestrator", settings=settings),
    )


def _build_domain_services(infra) -> dict[str, Any]:
    logger = StandardLogger()

    input_guard = InputGuard(settings.INPUT_GUARD_CONFIG, logger)
    output_guard = OutputGuard(
        response_validator=None,
        config=settings.OUTPUT_GUARD_CONFIG,
        logger=logger,
    )
    intent_classifier = IntentClassifier()
    response_formatter = ResponseFormatter(log=logger)
    agent_selector = AgentSelector()
    

    return {
        "input_guard": input_guard,
        "output_guard": output_guard,
        "intent_classifier": intent_classifier,
        "response_formatter": response_formatter,
        "agent_selector": agent_selector,
    }


def _build_available_dependencies(infra, services: dict[str, Any]) -> dict[str, Any]:
    uuid_builder = UuidBuilder()

    # SA-892 (N10): one shared conv_id -> set[run_id] index instance. The DI
    # loader injects it by name into every use case that declares the param,
    # so the writer (agent-action) and reader (stop) share the same store.
    conversation_run_index = InMemoryConversationRunIndex()

    return {
        "settings": settings,
        "logger": StandardLogger(),
        "conversation_run_index": conversation_run_index,
        "permission_provider": infra.permission_provider,
        "llm_provider": infra.llm_provider,
        "input_guard": services["input_guard"],
        "output_guard": services["output_guard"],
        "intent_classifier": services["intent_classifier"],
        "response_formatter": services["response_formatter"],
        "agent_registry_client": infra.agent_registry_client,
        "workflow_client": infra.workflow_client,
        "agent_selector": services["agent_selector"],
        "uuid_builder": uuid_builder,
        # SA-1948: injected by constructor-param name into OrchestrateGraphUseCase.
        "context_budget_manager": _build_context_budget_manager(),
    }


def build_dependencies():
    infra = create_infrastructure(
        settings,
    )
    services = _build_domain_services(infra)
    dependencies = _build_available_dependencies(
        infra,
        services,
    )
    
    return dependencies