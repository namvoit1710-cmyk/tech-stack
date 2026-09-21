from unittest.mock import MagicMock, patch


def _configure_bootstrap_settings(
    mock_settings: MagicMock, *, hana_host: str = ""
) -> None:
    mock_settings.DEFAULT_TENANT_ID = "default"
    mock_settings.OPENAI_API_KEY = ""
    mock_settings.APP_MODE = "SERVER"
    mock_settings.MCP_SERVERS = []
    mock_settings.MCP_TOOL_FILTER = []
    mock_settings.LLM_MODEL = ""
    mock_settings.HANA_HOST = hana_host


@patch("agent_sdk.bootstrap.settings")
def test_build_app_container_respects_injected_shared_state_repository(mock_settings):
    from agent_sdk.bootstrap import build_app_container

    _configure_bootstrap_settings(mock_settings)
    shared_state_repository = object()

    container = build_app_container(
        extra_dependencies={"shared_state_repository": shared_state_repository}
    )

    assert (
        container["_dependencies"]["shared_state_repository"] is shared_state_repository
    )


@patch("agent_sdk.bootstrap.settings")
def test_build_app_container_auto_wires_hana_shared_state_repository(mock_settings):
    from agent_sdk.bootstrap import build_app_container

    _configure_bootstrap_settings(mock_settings, hana_host="hana.internal")

    with patch(
        "agent_sdk.layer4_frameworks.persistence.hana.hana_connection_manager.HanaConnectionManager"
    ) as hana_connection_manager_cls, patch(
        "agent_sdk.layer4_frameworks.persistence.hana.hana_shared_state_repository.HanaSharedStateRepository"
    ) as hana_shared_state_repository_cls:
        hana_connection_manager = MagicMock()
        shared_state_repository = MagicMock()
        hana_connection_manager_cls.return_value = hana_connection_manager
        hana_shared_state_repository_cls.return_value = shared_state_repository

        container = build_app_container()

    assert (
        container["_dependencies"]["hana_connection_manager"] is hana_connection_manager
    )
    assert (
        container["_dependencies"]["shared_state_repository"] is shared_state_repository
    )
    shared_state_repository.setup.assert_called_once_with()
