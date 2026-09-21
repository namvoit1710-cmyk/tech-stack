"""
Executor Runtime Service — Entrypoint.

Thin entrypoint that delegates all wiring to bootstrap.py.
For CONSUMER mode (default), uses agent_sdk.run_consumer_agent() with the
service-owned container built by bootstrap.py.
"""

import asyncio

import uvicorn

from bootstrap import build_app_container
from app.layer4_frameworks.config.app_config import settings


def create_app():
    from agent_sdk import create_agent_app

    container = build_app_container()
    return create_agent_app(container)


app = create_app() if settings.APP_MODE.upper() in {"SERVER", "HYBRID"} else None


def run() -> None:
    mode = settings.APP_MODE.upper()

    if mode == "CONSUMER":
        from agent_sdk import run_consumer_agent

        container = build_app_container()
        asyncio.run(run_consumer_agent(container))
        return

    if mode in {"SERVER", "HYBRID"}:
        uvicorn.run(
            app or create_app(),
            host=getattr(settings, "SERVER_HOST", "0.0.0.0"),
            port=settings.SERVER_PORT,
        )
        return

    raise ValueError(f"Unknown APP_MODE={mode}. Use SERVER, CONSUMER, or HYBRID.")


if __name__ == "__main__":
    run()
