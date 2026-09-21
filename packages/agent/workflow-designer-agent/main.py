import asyncio

import uvicorn
from agent_sdk import create_agent_app, run_consumer_agent

from app.layer3_adapters.controllers.restful.v1.design_stream_controller import (
    ConversationStore,
    router as design_stream_router,
)
from app.layer4_frameworks.config.settings_adapter import settings
from bootstrap import build_app_container, get_design_workflow_use_case


def create_app():
    container = build_app_container()
    app = create_agent_app(container)
    app.state.container = container
    # Expose the design flow to the SSE progress endpoint.
    app.state.design_use_case = get_design_workflow_use_case()
    # Per-process conversation history so the stream can thread multi-turn clarify.
    app.state.conversation_store = ConversationStore()
    app.include_router(design_stream_router)
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
