import logging

from agent_sdk import (
    AgentGraphBuilder,
    build_app_container as _sdk_build_app_container,
    create_checkpointer,
)
from app.layer2_application.features.main_agent.use_cases import load_features
from app.layer2_application.queues.workflow_queue_handler import (
    handle_workflow_queue_message,
)
from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.graph.graph_builder import (
    build_graph,
)
from app.layer4_frameworks.dependency.ioc_container import build_dependencies
from app.layer4_frameworks.dependency.ioc_container import build_tools

logger = logging.getLogger(__name__)


def build_app_container() -> dict:
    checkpointer = create_checkpointer(settings)
    graph_deps = build_dependencies()

    return _sdk_build_app_container(
        agent_graph=build_graph(
            deps=graph_deps,
            checkpointer=checkpointer,
            builder_factory=AgentGraphBuilder,
        ),
        extra_dependencies={
            "settings": settings,
            **graph_deps,
            "workflow_queue_handler": handle_workflow_queue_message,
        },
        local_tools=build_tools(),
    )
