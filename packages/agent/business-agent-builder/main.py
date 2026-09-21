import os

import uvicorn

from bootstrap import build_app_container
from app.layer4_frameworks.config.app_config import settings


def create_app():
    from agent_sdk import create_agent_app
    container = build_app_container()
    return create_agent_app(container)


app = create_app()


if __name__ == "__main__":
    # Cloud Foundry injects the port to bind via $PORT and its health check probes it.
    # Honor $PORT when present; fall back to SERVER_PORT for local runs.
    port = int(os.environ.get("PORT") or settings.SERVER_PORT)
    uvicorn.run(app, host="0.0.0.0", port=port)
