"""Bootstrap and app-structure regression tests for migration work."""

import importlib
import sys
from unittest.mock import sentinel
from unittest.mock import AsyncMock, MagicMock

import pytest

import bootstrap as bootstrap_module


class _FakeUseCase:
    def __init__(self, **_kwargs):
        self.kwargs = _kwargs


def test_build_app_container_exposes_runtime_components(monkeypatch):
    dependencies = {
        "hana_connection_manager": sentinel.hana_connection_manager,
        "logger": sentinel.logger,
        "consumer": sentinel.consumer,
        "push_gateway_notifier": sentinel.push_gateway_notifier,
        "infra": sentinel.infra,
    }

    monkeypatch.setattr(
        bootstrap_module,
        "create_checkpointer",
        lambda settings: sentinel.checkpointer,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "build_dependencies",
        lambda: dependencies,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "build_graph",
        lambda deps, checkpointer=None, builder_factory=None: {
            "deps": deps,
            "checkpointer": checkpointer,
            "builder_factory": builder_factory,
        },
    )
    monkeypatch.setattr(bootstrap_module, "migrate", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bootstrap_module,
        "_sdk_build_app_container",
        lambda agent_graph, extra_dependencies: {
            "_dependencies": extra_dependencies,
            "_agent_graph": agent_graph,
        },
    )

    monkeypatch.setattr(
        bootstrap_module,
        "load_all_usecases",
        lambda dependencies, logger=None, reload=False: {
            "orchestrate.orchestrate": _FakeUseCase(**dependencies),
            "conversation.create_conversation": _FakeUseCase(**dependencies),
            "conversation.list_conversations": _FakeUseCase(**dependencies),
        },
    )

    container = bootstrap_module.build_app_container()

    assert "_usecases" in container
    assert "orchestrate.orchestrate" in container["_usecases"]
    assert "conversation.create_conversation" in container["_usecases"]
    assert "conversation.list_conversations" in container["_usecases"]
    assert container["_dependencies"]["infra"] is sentinel.infra
    assert container["_dependencies"]["consumer"] is sentinel.consumer
    assert container["_dependencies"]["push_gateway_notifier"] is sentinel.push_gateway_notifier
    assert container["_agent_graph"]["deps"] is dependencies
    assert container["_agent_graph"]["checkpointer"] is sentinel.checkpointer


def test_create_app_keeps_current_routes(monkeypatch):
    monkeypatch.setattr(bootstrap_module, "build_app_container", lambda: {})
    sys.modules.pop("main", None)
    main_module = importlib.import_module("main")

    app = main_module.create_app()
    route_paths = {
        path for route in app.routes if (path := getattr(route, "path", None)) is not None
    }
    included_prefixes = {
        route.include_context.prefix
        for route in app.routes
        if getattr(route, "include_context", None) is not None
    }

    assert "/health" in route_paths
    assert "/orchestrate" in included_prefixes or any(
        path.startswith("/orchestrate") for path in route_paths
    )
    assert "/conversations" in included_prefixes or any(
        path.startswith("/conversations") for path in route_paths
    )


def test_importing_main_does_not_build_container(monkeypatch):
    calls = []

    def _fake_build_app_container():
        calls.append("built")
        return {}

    monkeypatch.setattr(bootstrap_module, "build_app_container", _fake_build_app_container)
    sys.modules.pop("main", None)

    main_module = importlib.import_module("main")

    assert hasattr(main_module, "create_app")
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.skip(reason="_lifespan was moved into agent_sdk's create_agent_app")
async def test_lifespan_starts_and_stops_runtime_dependencies(monkeypatch):
    sys.modules.pop("main", None)
    main_module = importlib.import_module("main")

    consumer = MagicMock()
    consumer.start = AsyncMock()
    consumer.stop = AsyncMock()
    push_notifier = MagicMock()
    push_notifier.close = AsyncMock()
    infra = MagicMock()

    app = main_module.create_app()
    app.state.container = {
        "_dependencies": {
            "consumer": consumer,
            "push_gateway_notifier": push_notifier,
            "infra": infra,
        }
    }

    async with main_module._lifespan(app):
        consumer.start.assert_awaited_once()

    consumer.stop.assert_awaited_once()
    push_notifier.close.assert_awaited_once()
    infra.close.assert_called_once_with()


@pytest.mark.skip(reason="get_app_container was removed from main.py")
def test_get_app_container_is_cached(monkeypatch):
    sys.modules.pop("main", None)
    main_module = importlib.import_module("main")
    main_module.get_app_container.cache_clear()
    calls = []

    monkeypatch.setattr(main_module, "build_app_container", lambda: calls.append("built") or {"ok": True})

    first = main_module.get_app_container()
    second = main_module.get_app_container()

    assert first == {"ok": True}
    assert second == {"ok": True}
    assert calls == ["built"]
