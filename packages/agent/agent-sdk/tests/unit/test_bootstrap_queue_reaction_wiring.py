from unittest.mock import MagicMock, patch


def _configure_bootstrap_settings(mock_settings: MagicMock) -> None:
    mock_settings.DEFAULT_TENANT_ID = "default"
    mock_settings.OPENAI_API_KEY = ""
    mock_settings.APP_MODE = "SERVER"
    mock_settings.MCP_SERVERS = []
    mock_settings.MCP_TOOL_FILTER = []
    mock_settings.LLM_MODEL = ""
    mock_settings.HANA_HOST = ""
    mock_settings.KAFKA_REQUEST_TOPIC = "agent.request.compat"


@patch("agent_sdk.bootstrap.settings")
def test_build_app_container_auto_wires_queue_reaction_router_and_delegator(
    mock_settings,
):
    from agent_sdk.bootstrap import build_app_container
    from agent_sdk.layer2_application.services.async_agent_delegator import (
        AsyncAgentDelegator,
    )
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    _configure_bootstrap_settings(mock_settings)
    publisher = object()
    agent_registry = object()
    workflow_queue_handler = MagicMock()

    container = build_app_container(
        extra_dependencies={
            "publisher": publisher,
            "agent_registry": agent_registry,
            "message_reaction_handlers": {"workflow.queue": workflow_queue_handler},
        }
    )

    assert isinstance(
        container["_dependencies"]["agent_delegator"], AsyncAgentDelegator
    )
    assert isinstance(container["message_reaction_router"], MessageReactionRouter)
    assert (
        container["_dependencies"]["correlation_threads"]
        is container["message_reaction_router"]._correlation_threads
    )
    assert (
        container["_dependencies"]["correlation_threads"]
        is container["_dependencies"]["agent_delegator"]._correlation_threads
    )
    assert (
        container["message_reaction_router"]._custom_handlers["workflow.queue"]
        is workflow_queue_handler
    )
