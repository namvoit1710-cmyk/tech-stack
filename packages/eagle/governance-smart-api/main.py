import os

from fastapi import FastAPI
import uvicorn
from smart_service_sdk import smart_create_app
from app.bootstrap import build_governance_dependencies
from app.layer3_adapters.controllers.restful.v1 import (
    governance_ui_console_controller,
    governance_import_controller,
)


def create_app() -> FastAPI:
    app = smart_create_app(container_enricher=build_governance_dependencies)
    api_prefix = "/api/v1"
    app.include_router(governance_import_controller.router, prefix=api_prefix)
    app.include_router(governance_ui_console_controller.router, prefix=api_prefix)

    # Platform liveness probe at the root path. Dependency-free (no HANA / SDK
    # container) so the CloudFoundry health check has a stable endpoint to hit
    # regardless of the SDK's /api/v1 routes.
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app

app = create_app()


if __name__ == "__main__":
    # CloudFoundry injects $PORT at runtime; default to 8080 for local runs.
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
