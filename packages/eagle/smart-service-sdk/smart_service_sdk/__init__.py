from contextlib import asynccontextmanager
import inspect
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from fastapi import FastAPI

from smart_service_sdk.bootstrap import build_app_container_async
from smart_service_sdk.layer3_adapters.controllers.restful.v1 import (
    background_job_controller,
    field_config_controller,
    material_sds_analysis_controller,
    similarity_controller,
    ui_console_controller,
)
from smart_service_sdk.layer4_frameworks.config.app_config import settings
from smart_service_sdk.layer3_adapters.controllers.restful.v1 import cleansing_enrichment_controller, health_controller, rule_suggestion_controller, search_controller

ContainerEnricher = Callable[[dict], dict | None | Awaitable[dict | None]]


@asynccontextmanager
async def lifespan(
    app: FastAPI,
    container_enricher: ContainerEnricher | None = None,
    *,
    extra_schema_sql_paths: Sequence[str | Path] | None = None,
    extra_seed_sql_paths: Sequence[str | Path] | None = None,
):
    app.state.container = await build_app_container_async(
        extra_schema_sql_paths=extra_schema_sql_paths,
        extra_seed_sql_paths=extra_seed_sql_paths,
    )
    if container_enricher is not None:
        enrichment = container_enricher(app.state.container)
        if inspect.isawaitable(enrichment):
            enrichment = await enrichment
        if isinstance(enrichment, dict):
            app.state.container.update(enrichment)
    background_job_coordinator = app.state.container.get("background_job_coordinator")
    if (
        background_job_coordinator is not None
        and settings.ENABLE_RESUME_RUNNING_BACKGROUND_JOBS
    ):
        await background_job_coordinator.resume_running_jobs()
    try:
        yield
    finally:
        app.state.container.clear()


def smart_create_app(
    *,
    container_enricher: ContainerEnricher | None = None,
    extra_schema_sql_paths: Sequence[str | Path] | None = None,
    extra_seed_sql_paths: Sequence[str | Path] | None = None,
) -> FastAPI:
    
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        lifespan=lambda app: lifespan(
            app,
            container_enricher,
            extra_schema_sql_paths=extra_schema_sql_paths,
            extra_seed_sql_paths=extra_seed_sql_paths,
        ),
    )

    api_prefix = "/api/v1"
    app.include_router(health_controller.router, prefix=api_prefix)
    app.include_router(background_job_controller.router, prefix=api_prefix)
    app.include_router(cleansing_enrichment_controller.router, prefix=api_prefix)
    app.include_router(material_sds_analysis_controller.router, prefix=api_prefix)
    app.include_router(rule_suggestion_controller.router, prefix=api_prefix)
    app.include_router(search_controller.router, prefix=api_prefix)
    app.include_router(similarity_controller.router, prefix=api_prefix)
    app.include_router(field_config_controller.router, prefix=api_prefix)
    app.include_router(ui_console_controller.router, prefix=api_prefix)
    return app
