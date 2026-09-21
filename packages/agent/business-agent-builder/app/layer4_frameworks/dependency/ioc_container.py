from agent_sdk import HanaConnectionManager, OpenAIService
from agent_sdk.layer4_frameworks.logging.standard import StandardLogger

from app.layer2_application.features.main_agent.use_cases import (
    load_all_usecases,
    load_features,
)
from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.repositories.shared_state_repository import (
    SharedStateRepository,
)
from app.layer4_frameworks.tools.tool_registry import scan_all_tools


def build_tools():
    return [*scan_all_tools()]


def build_dependencies():
    ioc_container: dict = {
        "settings": settings,
        "logger": StandardLogger(),
        "shared_state_repository": SharedStateRepository(),
        "worker_tools": build_tools(),
    }

    # LLM is optional: it needs BOTH a model and an API key. Without them the builder
    # uses a deterministic heuristic draft, so the service boots fine (e.g. on Cloud
    # Foundry where LLM_MODEL is set but OPENAI_API_KEY is provided separately/absent).
    # OpenAIService() raises if the key is empty, so guard on the key too.
    if settings.LLM_MODEL and settings.OPENAI_API_KEY:
        ioc_container["openai_service"] = OpenAIService(
            api_key=settings.OPENAI_API_KEY,
            model=settings.LLM_MODEL,
            temperature=getattr(settings, "LLM_TEMPERATURE", 0.2),
        )

    # HANA is optional for this agent.
    if settings.HANA_HOST:
        db = HanaConnectionManager(settings)
        db.initialize()
        ioc_container["hana_db"] = db

    load_features(ioc_container["logger"])
    load_all_usecases(ioc_container, ioc_container["logger"])

    return ioc_container
