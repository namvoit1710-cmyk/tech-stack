from app.layer1_domain.entities.modify_file_state import ModifyFileState
from app.layer4_frameworks.graph import build_graph
from agent_sdk import build_app_container as _sdk_build_app_container
from agent_sdk import AgentGraphBuilder, OpenAIService, format_response_node
from app.layer4_frameworks.config.settings_adapter import AppSettingsAdapter

def build_app_container() -> dict:
    deps = {
        "app_settings_adapter": AppSettingsAdapter(),
        "open_ai_service": OpenAIService(),
        "agent_graph_builder": AgentGraphBuilder(state_schema=ModifyFileState),
        "format_response_node": format_response_node,
    }
    return _sdk_build_app_container(
        agent_graph=build_graph(deps)
    )
