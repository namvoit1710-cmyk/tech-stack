import asyncio
from collections.abc import Callable
from typing import Any


class _UvicornProxy:
    def __getattr__(self, name: str) -> Any:
        import uvicorn as _uvicorn

        return getattr(_uvicorn, name)


uvicorn = _UvicornProxy()

settings = None
build_app_container = None
create_agent_app = None
run_consumer_agent = None

_APP_MODE_SERVER = "SERVER"
_APP_MODE_CONSUMER = "CONSUMER"
_APP_MODE_HYBRID = "HYBRID"


def _resolve_settings() -> Any:
    _g = globals()
    if _g["settings"] is None:
        from agent_sdk.layer4_frameworks.config.app_config import settings as _s

        _g["settings"] = _s
    return _g["settings"]


def _resolve_build_app_container() -> Any:
    _g = globals()
    if _g["build_app_container"] is None:
        from agent_sdk.bootstrap import build_app_container as _f

        _g["build_app_container"] = _f
    return _g["build_app_container"]


def _resolve_create_agent_app() -> Any:
    _g = globals()
    if _g["create_agent_app"] is None:
        from agent_sdk.layer3_adapters.presenters.agent_server import (
            create_agent_app as _f,
        )

        _g["create_agent_app"] = _f
    return _g["create_agent_app"]


def _resolve_run_consumer_agent() -> Any:
    _g = globals()
    if _g["run_consumer_agent"] is None:
        from agent_sdk.layer3_adapters.presenters.agent_consumer import (
            run_consumer_agent as _f,
        )

        _g["run_consumer_agent"] = _f
    return _g["run_consumer_agent"]


def run_agent(
    features_path: str | None = None,
    base_module: str | None = None,
    extra_dependencies: dict[str, Any] | None = None,
    agent_graph: Any | None = None,
    agent_graph_factory: Callable[[dict[str, Any]], Any] | None = None,
    local_tools: list[Any] | None = None,
    dependency_overrides: dict[str, Any] | None = None,
    dependency_factories: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
    container_hooks: list[Callable[[dict[str, Any]], None]] | None = None,
    inbound_message_adapter: Callable[[Any], Any] | None = None,
    custom_message_handlers: dict[str, Any] | None = None,
    container_factory: Callable[[], dict[str, Any]] | None = None,
    app_configurer: Callable[[Any, dict[str, Any]], None] | None = None,
) -> None:
    _settings = _resolve_settings()
    if container_factory is not None:
        container = container_factory()
    else:
        _build = _resolve_build_app_container()
        build_kwargs: dict[str, Any] = {
            "features_path": features_path,
            "base_module": base_module,
            "extra_dependencies": extra_dependencies,
            "agent_graph": agent_graph,
            "agent_graph_factory": agent_graph_factory,
            "local_tools": local_tools,
        }

        # Preserve the old default call shape for compatibility with existing
        # callers/tests. Only pass new generic extension-point kwargs when the
        # caller actually provides them.
        if dependency_overrides is not None:
            build_kwargs["dependency_overrides"] = dependency_overrides
        if dependency_factories is not None:
            build_kwargs["dependency_factories"] = dependency_factories
        if container_hooks is not None:
            build_kwargs["container_hooks"] = container_hooks
        if inbound_message_adapter is not None:
            build_kwargs["inbound_message_adapter"] = inbound_message_adapter
        if custom_message_handlers is not None:
            build_kwargs["custom_message_handlers"] = custom_message_handlers

        container = _build(**build_kwargs)
    mode = _settings.APP_MODE.upper()
    if mode == _APP_MODE_SERVER:
        _create_app = _resolve_create_agent_app()
        app = _create_app(container)
        if app_configurer is not None:
            app_configurer(app, container)
        uvicorn.run(app, host=_settings.SERVER_HOST, port=_settings.SERVER_PORT)
    elif mode == _APP_MODE_CONSUMER:
        asyncio.run(_resolve_run_consumer_agent()(container))
    elif mode == _APP_MODE_HYBRID:
        dependencies = container.get("_dependencies", {})
        dependencies["start_consumer_with_server"] = True

        _create_app = _resolve_create_agent_app()
        app = _create_app(container)
        if app_configurer is not None:
            app_configurer(app, container)
        uvicorn.run(app, host=_settings.SERVER_HOST, port=_settings.SERVER_PORT)
    else:
        raise ValueError(
            f"Unknown APP_MODE: {mode}. "
            f"Use {_APP_MODE_SERVER}, {_APP_MODE_CONSUMER}, or {_APP_MODE_HYBRID}."
        )
