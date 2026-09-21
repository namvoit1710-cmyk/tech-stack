from agent_sdk.layer4_frameworks.config.app_config import settings
from agent_sdk.runner import run_agent


def create_app():
    """Backward-compatible factory for ``uvicorn main:app`` deployments."""
    from agent_sdk.bootstrap import build_app_container
    from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app

    container = build_app_container()
    return create_agent_app(container)


app = create_app() if settings.APP_MODE.upper() in {"SERVER", "HYBRID"} else None


if __name__ == "__main__":
    run_agent()
