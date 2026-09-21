import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent_sdk.layer1_domain.entities.agent_registration import AgentRegistration
from agent_sdk.layer3_adapters.presenters.agent_ops import (
    attach_ops_routes,
    build_agent_registration,
    build_agent_runtime_config,
    build_endpoint_url,
    build_execution_policy,
    build_queue_metadata,
    build_routing_metadata,
    managed_agent_lifecycle,
)
from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import create_router

logger = logging.getLogger(__name__)


def _build_routing_metadata(app_settings) -> dict:
    """Build routing metadata dict from Settings fields. Returns empty dict if all defaults."""
    return build_routing_metadata(app_settings)


def _build_endpoint_url(app_settings=None) -> str:
    """Build the endpoint URL for agent registration.

    On Cloud Foundry (VCAP_APPLICATION present), uses https with the CF URI.
    Locally, uses http with host:port.
    """
    return build_endpoint_url(app_settings)


def _build_agent_runtime_config(app_settings):
    return build_agent_runtime_config(app_settings)


def _build_execution_policy(app_settings):
    return build_execution_policy(app_settings)


def _build_queue_metadata(app_settings):
    return build_queue_metadata(app_settings)


def _build_agent_registration(
    app_settings, registered_tool_ids=None
) -> AgentRegistration:
    return build_agent_registration(app_settings, registered_tool_ids)


def _resolve_api_title(app_settings) -> str:
    if app_settings is None:
        return "Agent SDK Server"

    app_name = getattr(app_settings, "APP_NAME", "")
    if isinstance(app_name, str) and app_name.strip():
        return f"{app_name.strip()} API"

    agent_type = getattr(app_settings, "AGENT_TYPE", "")
    if isinstance(agent_type, str) and agent_type.strip():
        return f"{agent_type.strip()} API"

    return "Agent SDK Server"


def _resolve_api_version(app_settings) -> str:
    if app_settings is None:
        return "1.0.0"

    agent_version = getattr(app_settings, "AGENT_VERSION", "")
    if isinstance(agent_version, str) and agent_version.strip():
        return agent_version.strip()

    return "1.0.0"


def create_agent_app(container: dict) -> FastAPI:
    dependencies = container.get("_dependencies", {})
    settings = dependencies.get("settings")

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        dependencies = app.state.dependencies
        consumer_task: asyncio.Task | None = None
        shutdown_event: asyncio.Event | None = None

        async with managed_agent_lifecycle(dependencies):
            settings = dependencies.get("settings")
            mode = getattr(settings, "APP_MODE", "").upper() if settings else ""
            should_start_consumer = (
                dependencies.get("start_consumer_with_server") is True
                or mode == "HYBRID"
            )

            if should_start_consumer:
                from agent_sdk.layer3_adapters.presenters.agent_consumer import (
                    run_consumer_agent,
                )

                shutdown_event = asyncio.Event()
                dependencies["shutdown_event"] = shutdown_event

                consumer_task = asyncio.create_task(
                    run_consumer_agent(
                        container,
                        manage_lifecycle=False,
                        install_signal_handlers=False,
                        start_ops_app=False,
                    )
                )

                def _log_consumer_failure(task: asyncio.Task) -> None:
                    if task.cancelled():
                        return
                    exc = task.exception()
                    if exc is None:
                        return
                    logger.exception("Hybrid consumer task failed", exc_info=exc)
                    shutdown_event.set()

                consumer_task.add_done_callback(_log_consumer_failure)

            try:
                yield
            finally:
                if shutdown_event is not None:
                    shutdown_event.set()

                if consumer_task is not None:
                    with suppress(asyncio.CancelledError):
                        await consumer_task

    app = FastAPI(
        title=_resolve_api_title(settings),
        version=_resolve_api_version(settings),
        lifespan=_lifespan,
    )
    allow_origins = ["*"]
    if settings is not None:
        allow_origins = getattr(settings, "ALLOW_ORIGINS", ["*"])
    allow_credentials = False if not allow_origins or "*" in allow_origins else True
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.dependencies = dependencies
    attach_ops_routes(app, container)

    v1_router = create_router(container)
    app.include_router(v1_router, prefix="/api/v1")
    return app
