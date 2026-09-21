import uvicorn

from bootstrap import build_app_container
from app.layer4_frameworks.config.app_config import settings


def create_app():
    from agent_sdk import create_agent_app
    container = build_app_container()
    return create_agent_app(container)


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.SERVER_PORT)
