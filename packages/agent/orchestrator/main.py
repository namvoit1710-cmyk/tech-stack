import asyncio

import uvicorn
from functools import lru_cache
from agent_sdk import create_agent_app, run_consumer_agent

from app.layer4_frameworks.config.app_config import settings
from bootstrap import build_app_container
from app.layer3_adapters.controllers.restful.v1.orchestrator_controller import orchestrate_router
from app.layer3_adapters.controllers.restful.v1.conversation_controller import conversations_router



def create_app():
    container = build_app_container()
    app = create_agent_app(container)
    app.include_router(orchestrate_router, prefix="/orchestrate", tags=["orchestrate"])
    app.include_router(conversations_router, prefix="/conversations", tags=["conversations"])
    app.state.container = container
    return app


def run() -> None:
    mode = settings.APP_MODE.upper()

    if mode == "CONSUMER":
        container = build_app_container()
        asyncio.run(run_consumer_agent(container))
        return

    if mode in {"SERVER", "HYBRID"}:
        uvicorn.run(
            create_app(),
            host=getattr(settings, "SERVER_HOST", "0.0.0.0"),
            port=settings.SERVER_PORT,
        )
        return

    raise ValueError(f"Unknown APP_MODE={mode}. Use SERVER, CONSUMER, or HYBRID.")


if __name__ == "__main__":
    run()