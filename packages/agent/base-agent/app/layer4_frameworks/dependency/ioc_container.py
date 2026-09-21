from app.layer4_frameworks.clients.workflow_clients import WorkflowClient
from app.layer4_frameworks.repositories.shared_state_repository import SharedStateRepository
from app.layer4_frameworks.config.app_config import settings
from agent_sdk import OpenAIService, HanaConnectionManager
from agent_sdk.layer4_frameworks.logging.standard import StandardLogger
from app.layer4_frameworks.tools.tool_registry import scan_all_tools
from app.layer2_application.features.main_agent.use_cases import load_all_usecases, load_features

def build_tools():
    tools = [*scan_all_tools()]
    return tools


def build_dependencies():
    shared_state_repository = SharedStateRepository()
    workflow_client = WorkflowClient(settings.WORKFLOW_API_URL)

    ioc_container: dict = {
        "settings": settings,
        "logger": StandardLogger(),
        "shared_state_repository": shared_state_repository,
        "workflow_client": workflow_client,
        "worker_tools": build_tools(),
    }

    
    if settings.LLM_MODEL:
        ioc_container["openai_service"] = OpenAIService(
            api_key=settings.OPENAI_API_KEY,
            model=settings.LLM_MODEL,
            temperature=settings.LLM_TEMPERATURE,
            provider=settings.LLM_PROVIDER,
            timeout=settings.LLM_TIMEOUT,
            max_tokens=settings.LLM_MAX_TOKENS,
            max_retries=settings.LLM_MAX_RETRIES,
            model_kwargs=settings.LLM_MODEL_KWARGS
            or {"reasoning": {"effort": "medium"}},
        )

    # HANA is optional for the base blueprint.
    db = None
    if settings.HANA_HOST:
        db = HanaConnectionManager(settings)
        db.initialize()
        ioc_container["hana_db"] = db


    load_features(ioc_container["logger"])
    load_all_usecases(ioc_container, ioc_container["logger"])

    return ioc_container