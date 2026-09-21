from types import SimpleNamespace

import agent_sdk.layer4_frameworks.config.app_config as sdk_app_config
import agent_sdk.runner as sdk_runner
import bootstrap as bootstrap_module


def test_install_csv_settings_for_sdk_updates_both_sdk_settings_globals(monkeypatch):
    monkeypatch.setattr(sdk_runner, "settings", object(), raising=False)
    monkeypatch.setattr(sdk_app_config, "settings", object(), raising=False)

    bootstrap_module.install_csv_settings_for_sdk()

    assert sdk_runner.settings is bootstrap_module.settings
    assert sdk_app_config.settings is bootstrap_module.settings


def test_prepare_data_factory_mcp_settings_maps_data_factory_url_into_sdk_mcp_settings(
    monkeypatch,
):
    settings = SimpleNamespace(
        DATA_FACTORY_URL=" https://data-factory.example/mcp ",
        DATA_FACTORY_MCP_SERVER_NAME=" csv-data-factory ",
        DATA_FACTORY_MCP_TRANSPORT=" sse ",
        DATA_FACTORY_EXPOSE_RAW_MCP_TOOLS=False,
    )
    monkeypatch.setattr(bootstrap_module, "settings", settings)

    bootstrap_module._prepare_data_factory_mcp_settings()

    assert settings.MCP_SERVERS == [
        {
            "name": "csv-data-factory",
            "server_url": "https://data-factory.example/mcp",
            "transport": "sse",
        }
    ]
    assert settings.MCP_TOOL_FILTER == [
        bootstrap_module._INTERNAL_MCP_FILTER_SENTINEL
    ]


def test_prepare_data_factory_mcp_settings_preserves_explicit_servers_and_filters(
    monkeypatch,
):
    existing_servers = [{"name": "existing", "server_url": "https://configured"}]
    existing_filter = ["transform_row"]
    settings = SimpleNamespace(
        DATA_FACTORY_URL="https://data-factory.example/mcp",
        MCP_SERVERS=existing_servers,
        MCP_TOOL_FILTER=existing_filter,
        DATA_FACTORY_EXPOSE_RAW_MCP_TOOLS=False,
    )
    monkeypatch.setattr(bootstrap_module, "settings", settings)

    bootstrap_module._prepare_data_factory_mcp_settings()

    assert settings.MCP_SERVERS is existing_servers
    assert settings.MCP_TOOL_FILTER is existing_filter


def test_prepare_data_factory_mcp_settings_skips_hidden_filter_when_raw_tools_are_exposed(
    monkeypatch,
):
    settings = SimpleNamespace(
        DATA_FACTORY_URL="https://data-factory.example/mcp",
        DATA_FACTORY_EXPOSE_RAW_MCP_TOOLS=True,
    )
    monkeypatch.setattr(bootstrap_module, "settings", settings)

    bootstrap_module._prepare_data_factory_mcp_settings()

    assert settings.MCP_SERVERS == [
        {
            "name": "data_factory",
            "server_url": "https://data-factory.example/mcp",
            "transport": "streamable-http",
        }
    ]
    assert not hasattr(settings, "MCP_TOOL_FILTER")


def test_build_app_container_reuses_local_worker_tools_and_passes_runtime_hook(
    monkeypatch,
):
    local_tools = [object(), object()]
    kept_dependency = object()
    checkpointer = object()
    built_graph = object()
    call_order: list[str] = []
    captured: dict[str, object] = {}
    graph_factory_call: dict[str, object] = {}

    def _install_settings() -> None:
        call_order.append("install")

    def _prepare_mcp_settings() -> None:
        call_order.append("prepare")

    def _create_checkpointer(arg_settings):
        captured["checkpointer_settings"] = arg_settings
        return checkpointer

    def _build_dependencies() -> dict[str, object]:
        return {
            "worker_tools": local_tools,
            "local_worker_tools": [object()],
            "agent_graph": object(),
            "kept_dependency": kept_dependency,
        }

    def _build_graph(*, deps, checkpointer):
        graph_factory_call["deps"] = deps
        graph_factory_call["checkpointer"] = checkpointer
        return built_graph

    def _runtime_hook(container: dict[str, object]) -> None:
        container["runtime_hook_ran"] = True

    def _sdk_build_app_container(**kwargs):
        captured.update(kwargs)
        container = {"_dependencies": kwargs["extra_dependencies"]}
        for hook in kwargs["container_hooks"]:
            hook(container)
        return {
            "container": container,
            "graph": kwargs["agent_graph_factory"]({"sdk_dep": "value"}),
        }

    monkeypatch.setattr(
        bootstrap_module, "install_csv_settings_for_sdk", _install_settings
    )
    monkeypatch.setattr(
        bootstrap_module, "_prepare_data_factory_mcp_settings", _prepare_mcp_settings
    )
    monkeypatch.setattr(bootstrap_module, "create_checkpointer", _create_checkpointer)
    monkeypatch.setattr(bootstrap_module, "build_dependencies", _build_dependencies)
    monkeypatch.setattr(bootstrap_module, "build_graph", _build_graph)
    monkeypatch.setattr(
        bootstrap_module, "configure_sdk_runtime_gateways", _runtime_hook
    )
    monkeypatch.setattr(
        bootstrap_module, "_sdk_build_app_container", _sdk_build_app_container
    )

    result = bootstrap_module.build_app_container()

    assert call_order == ["install", "prepare"]
    assert captured["checkpointer_settings"] is bootstrap_module.settings
    assert captured["local_tools"] == local_tools
    assert captured["extra_dependencies"] == {
        "kept_dependency": kept_dependency,
        "settings": bootstrap_module.settings,
    }
    assert result["container"]["runtime_hook_ran"] is True
    assert result["graph"] is built_graph
    assert graph_factory_call == {
        "deps": {"sdk_dep": "value"},
        "checkpointer": checkpointer,
    }
