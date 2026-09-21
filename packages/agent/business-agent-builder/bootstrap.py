import logging

from agent_sdk import (
    AgentGraphBuilder,
    build_app_container as _sdk_build_app_container,
    create_checkpointer,
)
from app.layer2_application.queues.queue_handler import handle_queue_message
from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.dependency.ioc_container import build_dependencies, build_tools
from app.layer4_frameworks.graph.graph_builder import build_graph

logger = logging.getLogger(__name__)


def build_app_container() -> dict:
    # The SDK eagerly builds an OpenAI chat client at boot whenever LLM_MODEL is set
    # (it can come from a space-level env var, not just this app's manifest), which
    # raises "Missing credentials" without OPENAI_API_KEY and crashes the container.
    # Disable the LLM unless a key is actually present; the agent then runs in its
    # deterministic heuristic mode (prompt->config + keyword tool/agent matching).
    if not settings.OPENAI_API_KEY:
        settings.LLM_MODEL = ""

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
            "workflow_queue_handler": handle_queue_message,
        },
        local_tools=build_tools(),
    )
