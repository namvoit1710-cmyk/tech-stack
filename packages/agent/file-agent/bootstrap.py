from app.layer1_domain.entities.file_state import FileState
from app.layer4_frameworks.graph import build_graph
from agent_sdk import build_app_container as _sdk_build_app_container
from agent_sdk import AgentGraphBuilder, OpenAIService, create_checkpointer, format_response_node
from app.layer4_frameworks.config.settings_adapter import AppSettingsAdapter
from app.layer2_application.features.get_first_n_rows import get_first_n_rows
from app.layer2_application.features.plus import plus
from app.layer2_application.features.minus import minus
from app.layer2_application.features.get_weather_today import get_weather_today
from app.layer2_application.features.transform_row import transform_row   
from app.layer2_application.features.transform_column import transform_column
from app.layer2_application.features.create_file import create_file
from app.layer2_application.features.revert_file import revert_file
from app.layer2_application.features.import_file_to_node import import_file_to_node
from app.layer4_frameworks.data_factory.transformation_client import DataFactoryClient
from app.layer4_frameworks.impl.transformation_service_impl import DataFactoryService


class HeaderInferenceService:
    pass

def build_app_container() -> dict:
    app_settings = AppSettingsAdapter()
    checkpointer = create_checkpointer(app_settings)
    open_ai_service = OpenAIService()
    data_factory_client = DataFactoryClient(app_settings)
    data_factory_service = DataFactoryService(data_factory_client)
    deps = {
        "open_ai_service": open_ai_service,
        "agent_graph_builder": AgentGraphBuilder(state_schema=FileState),
        "format_response_node": format_response_node,
        "app_settings": app_settings,
        "checkpointer": checkpointer,
        "tool_registry": {
            plus.name: plus,
            minus.name: minus,
            get_weather_today.name: get_weather_today,
            transform_row.name: transform_row,
            transform_column.name: transform_column,
            get_first_n_rows.name: get_first_n_rows,
            create_file.name: create_file,
            revert_file.name: revert_file,
            import_file_to_node.name: import_file_to_node,
        },
    }
    return _sdk_build_app_container(
        agent_graph=build_graph(deps),
        extra_dependencies={
            "openai_service": open_ai_service,
            "data_factory_service": data_factory_service,
            "settings": app_settings,
        },
    )
