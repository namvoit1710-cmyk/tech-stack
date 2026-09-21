from __future__ import annotations

from agent_sdk import create_agent_app, run_agent

from app.layer4_frameworks.config.settings_adapter import settings
from bootstrap import build_app_container, install_csv_settings_for_sdk


def create_app():
    install_csv_settings_for_sdk()
    container = build_app_container()
    return create_agent_app(container)


app = None


if __name__ == "__main__":
    install_csv_settings_for_sdk()
    run_agent(container_factory=build_app_container)