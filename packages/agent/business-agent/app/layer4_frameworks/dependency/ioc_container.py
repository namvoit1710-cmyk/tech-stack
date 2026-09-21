from app.layer4_frameworks.config.app_config import settings
from agent_sdk.layer4_frameworks.logging.standard import StandardLogger
from app.layer4_frameworks.clients.agent_hub_client import AgentHubClient
from app.layer4_frameworks.clients.workflow_client import WorkflowClient
from app.layer4_frameworks.base.uuid_builder import UuidBuilder
from app.layer4_frameworks.clients.rag_client import RagClient
from app.layer4_frameworks.clients.file_client import FileClient

def build_tools():
    return None


def build_dependencies():
    logger = StandardLogger()

    agent_hub_client = AgentHubClient(settings.AGENT_HUB_API_URL, logger)
    workflow_client = WorkflowClient(settings.WORKFLOW_API_URL, logger)
    file_client = FileClient(settings.FILE_CLIENT_URL, logger)
    uuid_builder = UuidBuilder()

    rag_client = RagClient(
        base_url=settings.RAG_API_URL,
        query_path=settings.RAG_RETRIEVAL_QUERY_PATH,
        tenant_id=settings.RAG_TENANT_ID,
        top_k=settings.RAG_TOP_K,
        timeout_seconds=settings.RAG_TIMEOUT_SECONDS,
        logger=logger,
    )

    # SDK auto-creates from env/settings:
    # - llm_service (LangChain ChatModel from LLM_MODEL + OPENAI_API_KEY)
    # - push_gateway_notifier (PUSH_GATEWAY_URL + PUSH_GATEWAY_TRANSPORT=http)
    # - agent_shared_state_repository (HANA_HOST)
    # - shared_state_repository (HANA_HOST)
    # - publisher (MESSAGING_MODE + Kafka settings)

    ioc_container: dict = {
        "settings": settings,
        "logger": logger,
        "agent_hub_client": agent_hub_client,
        "workflow_client": workflow_client,
        "uuid_builder": uuid_builder,
        "rag_client": rag_client,
        "file_client": file_client,
    }

    return ioc_container
