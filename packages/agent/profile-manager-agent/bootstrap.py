from app.layer2_application.features.execute_agent.use_cases.process_profile_query_use_case import (
    ProcessProfileQueryUseCase,
)
from app.layer2_application.features.execute_agent.use_cases.execute_graph_use_case import (
    ExecuteGraphUseCase,
)
from app.layer4_frameworks.auth.oauth_token_provider import OAuthTokenProvider
from app.layer4_frameworks.catalog.operation_catalog import OperationCatalog
from app.layer4_frameworks.graph.graph import build_profile_manager_graph
from app.layer4_frameworks.graph.state import OdataQueryState
from app.layer4_frameworks.odata_services.user_executor import UserQueryRepository
from app.layer4_frameworks.planner.openai_planner import OpenAIPlanner
from agent_sdk import build_app_container as _sdk_build_app_container
from agent_sdk import AgentGraphBuilder, OpenAIService, format_response_node


def build_app_container() -> tuple[dict, ExecuteGraphUseCase, OAuthTokenProvider]:
    # --- compose layer4 concretes into layer2 ports (Dependency Inversion) ---
    open_ai_service = OpenAIService()
    catalog = OperationCatalog()
    planner = OpenAIPlanner(llm=open_ai_service, catalog=catalog)  # P3: LLM injected
    user_query = UserQueryRepository()  # async, IUserQueryPort

    # --- the use case owns the flow; concretes meet ports only here ---
    process_uc = ProcessProfileQueryUseCase(planner=planner, user_query=user_query)

    deps = {
        "process_profile_query_use_case": process_uc,
        "agent_graph_builder": AgentGraphBuilder(state_schema=OdataQueryState),
        "format_response_node": format_response_node,
    }
    graph = build_profile_manager_graph(deps)
    container = _sdk_build_app_container(agent_graph=graph)
    use_case = ExecuteGraphUseCase(graph=graph)
    token_provider = OAuthTokenProvider()
    return container, use_case, token_provider
