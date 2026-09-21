import asyncio
from fastapi import FastAPI
import uvicorn
from agent_sdk import create_agent_app, run_consumer_agent
from app.layer4_frameworks.config.settings_adapter import settings
from app.layer4_frameworks.middleware.auth import AuthMiddleware
from app.layer4_frameworks.middleware.exception_handler import ExceptionHandlerMiddleware
from bootstrap import build_app_container


def create_app():
    container, use_case, token_provider = build_app_container()
    app: FastAPI = create_agent_app(container)

    from app.layer3_adapters.controllers.restful.v1.profile_manager_controller import (
        create_router as create_custom_router,
    )

    custom_router = create_custom_router(use_case)

    # Prepend our /execute route so it takes priority over the SDK's generic one,
    # which does not inject the Bearer token into the graph state.
    # app.router.routes = [
    # POST /api/v1/execute  ← ours (added first → wins)
    # GET  /api/v1/info     ← SDK
    # POST /api/v1/execute  ← SDK (registered second → never reached)
    # POST /api/v1/resume   ← SDK
    # GET  /health          ← SDK
    # ...
    # ]
    # This is the fine approach for now, but we may want to consider a more robust solution in the future.
    sdk_routes = list(app.router.routes)
    app.router.routes.clear()
    app.include_router(custom_router, prefix="/api/v1")
    app.router.routes.extend(sdk_routes)
    
    # Add AuthMiddleware to the FastAPI app
    app.add_middleware(AuthMiddleware, token_provider=token_provider)

    app.add_middleware(ExceptionHandlerMiddleware)

    return app


def run() -> None:
    mode = settings.APP_MODE.upper()

    if mode == "CONSUMER":
        container, _, _ = build_app_container()
        asyncio.run(run_consumer_agent(container))
        return

    if mode in {"SERVER", "HYBRID"}:
        
        app: FastAPI = create_app()
        
        uvicorn.run(
            app,
            host=getattr(settings, "SERVER_HOST", "0.0.0.0"),
            port=settings.SERVER_PORT,
        )
        return

    raise ValueError(f"Unknown APP_MODE={mode}. Use SERVER, CONSUMER, or HYBRID.")


if __name__ == "__main__":
    run()