import logging
import os
import sys

# Ensure the SDK is in the path
sdk_root = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.abspath(sdk_root))

from app.layer2_application.echo_nodes import get_current_time
from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.graph.echo_graph_builder import build_echo_graph

from agent_sdk import (
    HanaConnectionManager,
    create_checkpointer,
    make_openai_service,
)
from agent_sdk import (
    build_app_container as _sdk_build_app_container,
)

logger = logging.getLogger(__name__)


class _NoopToolRegistry:
    async def sync_tools(self, registrations):
        return {}

    async def close(self):
        return None


def build_app_container() -> dict:
    checkpointer = create_checkpointer(settings)

    graph_deps: dict = {}
    local_tools = [get_current_time]
    if settings.LLM_MODEL:
        graph_deps["openai_service"] = make_openai_service(
            model_kwargs=settings.LLM_MODEL_KWARGS,
        )
    graph_deps["worker_tools"] = local_tools

    # HANA is optional for echo agent example
    db = None
    if settings.HANA_HOST:
        db = HanaConnectionManager(settings)
        db.initialize()
        graph_deps["hana_db"] = db

    # ``build_app_container`` auto-wires the publisher (and consumer when
    # APP_MODE != SERVER) from ``MESSAGING_MODE`` via ``create_messaging``.
    # Override by adding "publisher" or "consumer" to ``extra_dependencies``
    # only when you need a custom transport implementation.
    return _sdk_build_app_container(
        agent_graph=build_echo_graph(deps=graph_deps, checkpointer=checkpointer),
        extra_dependencies={
            "settings": settings,
            **graph_deps,
            "tool_registry": _NoopToolRegistry(),
        },
        local_tools=local_tools,
    )
