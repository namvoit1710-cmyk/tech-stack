import importlib

import agent_sdk.layer4_frameworks.config.app_config as sdk_app_config
import agent_sdk.runner as sdk_runner


def test_install_csv_settings_for_sdk_updates_sdk_globals(monkeypatch):
    bootstrap = importlib.import_module("bootstrap")

    monkeypatch.setattr(sdk_runner, "settings", object(), raising=False)
    monkeypatch.setattr(sdk_app_config, "settings", object(), raising=False)

    bootstrap.install_csv_settings_for_sdk()

    assert sdk_runner.settings is bootstrap.settings
    assert sdk_app_config.settings is bootstrap.settings


def test_prepare_data_factory_mcp_settings_sets_defaults(monkeypatch):
    bootstrap = importlib.import_module("bootstrap")

    monkeypatch.setattr(
        bootstrap.settings, "DATA_FACTORY_URL", "http://mcp.example", raising=False
    )
    monkeypatch.setattr(
        bootstrap.settings, "DATA_FACTORY_MCP_SERVER_NAME", "factory", raising=False
    )
    monkeypatch.setattr(
        bootstrap.settings, "DATA_FACTORY_MCP_TRANSPORT", "sse", raising=False
    )
    monkeypatch.setattr(
        bootstrap.settings,
        "DATA_FACTORY_EXPOSE_RAW_MCP_TOOLS",
        False,
        raising=False,
    )
    monkeypatch.setattr(bootstrap.settings, "MCP_SERVERS", [], raising=False)
    monkeypatch.setattr(bootstrap.settings, "MCP_TOOL_FILTER", [], raising=False)

    bootstrap._prepare_data_factory_mcp_settings()

    assert bootstrap.settings.MCP_SERVERS == [
        {
            "name": "factory",
            "server_url": "http://mcp.example",
            "transport": "sse",
        }
    ]
    assert bootstrap.settings.MCP_TOOL_FILTER == [
        bootstrap._INTERNAL_MCP_FILTER_SENTINEL
    ]


def test_prepare_data_factory_mcp_settings_preserves_existing_configuration(monkeypatch):
    bootstrap = importlib.import_module("bootstrap")

    existing_servers = [
        {
            "name": "existing",
            "server_url": "http://existing.example",
            "transport": "streamable-http",
        }
    ]
    existing_filter = ["list_rule_sets"]

    monkeypatch.setattr(
        bootstrap.settings, "DATA_FACTORY_URL", "http://mcp.example", raising=False
    )
    monkeypatch.setattr(
        bootstrap.settings,
        "DATA_FACTORY_EXPOSE_RAW_MCP_TOOLS",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        bootstrap.settings, "MCP_SERVERS", list(existing_servers), raising=False
    )
    monkeypatch.setattr(
        bootstrap.settings, "MCP_TOOL_FILTER", list(existing_filter), raising=False
    )

    bootstrap._prepare_data_factory_mcp_settings()

    assert bootstrap.settings.MCP_SERVERS == existing_servers
    assert bootstrap.settings.MCP_TOOL_FILTER == existing_filter


def test_prepare_data_factory_mcp_settings_skips_hidden_filter_when_raw_tools_are_exposed(
    monkeypatch,
):
    bootstrap = importlib.import_module("bootstrap")

    monkeypatch.setattr(
        bootstrap.settings, "DATA_FACTORY_URL", "http://mcp.example", raising=False
    )
    monkeypatch.setattr(
        bootstrap.settings,
        "DATA_FACTORY_EXPOSE_RAW_MCP_TOOLS",
        True,
        raising=False,
    )
    monkeypatch.setattr(bootstrap.settings, "MCP_SERVERS", [], raising=False)
    monkeypatch.setattr(bootstrap.settings, "MCP_TOOL_FILTER", [], raising=False)

    bootstrap._prepare_data_factory_mcp_settings()

    assert bootstrap.settings.MCP_SERVERS == [
        {
            "name": "data_factory",
            "server_url": "http://mcp.example",
            "transport": "streamable-http",
        }
    ]
    assert bootstrap.settings.MCP_TOOL_FILTER == []


def test_build_app_container_passes_sdk_hook_and_local_tools(monkeypatch):
    bootstrap = importlib.import_module("bootstrap")
    calls: list[str] = []
    tools = [object()]
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        bootstrap, "install_csv_settings_for_sdk", lambda: calls.append("install")
    )
    monkeypatch.setattr(
        bootstrap,
        "_prepare_data_factory_mcp_settings",
        lambda: calls.append("mcp"),
    )
    monkeypatch.setattr(bootstrap, "create_checkpointer", lambda _settings: "cp")
    monkeypatch.setattr(
        bootstrap,
        "build_dependencies",
        lambda: {"worker_tools": tools, "settings_provider": object()},
    )

    def _fake_sdk_build_app_container(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(
        bootstrap, "_sdk_build_app_container", _fake_sdk_build_app_container
    )

    result = bootstrap.build_app_container()

    assert calls == ["install", "mcp"]
    assert captured["local_tools"] == tools
    assert "worker_tools" not in captured["extra_dependencies"]
    assert "local_worker_tools" not in captured["extra_dependencies"]
    assert captured["extra_dependencies"]["settings"] is bootstrap.settings
    assert captured["container_hooks"] == [bootstrap.configure_sdk_runtime_gateways]
    assert callable(captured["agent_graph_factory"])
    assert result["local_tools"] == tools
