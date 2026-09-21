from __future__ import annotations

import logging
from typing import Any, cast

from agent_sdk import (
    build_app_container as _sdk_build_app_container,
)
from agent_sdk import (
    create_checkpointer,
)

from app.layer4_frameworks.config.settings_adapter import settings
from app.layer4_frameworks.dependency.ioc_container import (
    build_dependencies,
    configure_sdk_runtime_gateways,
)
from app.layer4_frameworks.graph.graph_builder import build_graph

logger = logging.getLogger(__name__)

_INTERNAL_MCP_FILTER_SENTINEL = "__csv_agent_internal_mcp_client_only__"


def install_csv_settings_for_sdk() -> None:
    """Make SDK runtime helpers use the CSV Settings subclass.

    agent-sdk.runner resolves settings lazily for run_agent(), while the SDK LLM
    factory reads agent_sdk.layer4_frameworks.config.app_config.settings. Keep
    both pointed at the CSV settings instance so direct build_app_container()
    calls, uvicorn factories, and run_agent(container_factory=...) use the same
    runtime configuration.
    """

    import agent_sdk.layer4_frameworks.config.app_config as _sdk_app_config
    import agent_sdk.runner as _sdk_runner

    cast(Any, _sdk_runner).settings = settings
    cast(Any, _sdk_app_config).settings = settings


def _clean_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _build_csv_graph_factory(checkpointer: Any):
    """Return a graph factory that receives the final SDK dependency set."""

    def _factory(sdk_dependencies: dict[str, Any]) -> Any:
        return build_graph(
            deps=sdk_dependencies,
            checkpointer=checkpointer,
        )

    return _factory


def _prepare_data_factory_mcp_settings() -> None:
    """Map DATA_FACTORY_URL into SDK-native MCP settings.

    By default, raw MCP tools are hidden from worker_tools with a non-matching
    MCP_TOOL_FILTER. The high-level CSV tools still use the SDK mcp_client via
    DataFactoryGateway, so the planner sees transform_column/transform_row rather
    than low-level Data Factory schemas.
    """

    data_factory_url = _clean_string(getattr(settings, "DATA_FACTORY_URL", ""))
    if not data_factory_url:
        return

    if not getattr(settings, "MCP_SERVERS", None):
        setattr(
            settings,
            "MCP_SERVERS",
            [
                {
                    "name": _clean_string(
                        getattr(settings, "DATA_FACTORY_MCP_SERVER_NAME", "")
                    )
                    or "data_factory",
                    "server_url": data_factory_url,
                    "transport": _clean_string(
                        getattr(settings, "DATA_FACTORY_MCP_TRANSPORT", "")
                    )
                    or "streamable-http",
                }
            ],
        )

    expose_raw_tools = bool(
        getattr(settings, "DATA_FACTORY_EXPOSE_RAW_MCP_TOOLS", False)
    )
    if not expose_raw_tools and not getattr(settings, "MCP_TOOL_FILTER", None):
        setattr(settings, "MCP_TOOL_FILTER", [_INTERNAL_MCP_FILTER_SENTINEL])


def build_app_container() -> dict[str, Any]:
    """Build the CSV agent container through agent-sdk."""

    install_csv_settings_for_sdk()
    _prepare_data_factory_mcp_settings()
    checkpointer = create_checkpointer(settings)
    graph_deps = build_dependencies()

    # Pass local tools via the SDK's dedicated ``local_tools`` parameter so the
    # SDK can combine them with optional MCP tooling and expose the final list as
    # ``worker_tools`` to the graph.
    local_tools = list(graph_deps.pop("worker_tools", []) or [])
    graph_deps.pop("local_worker_tools", None)
    graph_deps.pop("agent_graph", None)
    graph_deps["settings"] = settings

    return _sdk_build_app_container(
        extra_dependencies=graph_deps,
        agent_graph_factory=_build_csv_graph_factory(checkpointer),
        local_tools=local_tools,
        container_hooks=[configure_sdk_runtime_gateways],
    )
