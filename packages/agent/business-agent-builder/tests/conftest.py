import importlib
import sys

import pytest
from fastapi.testclient import TestClient


class _StubUseCase:
    async def execute(self, *_args, **_kwargs):
        return {}


def install_usecase_package_stubs() -> None:
    usecases = importlib.import_module("app.layer2_application.features.main_agent.use_cases")
    usecases.load_features = lambda logger: None
    usecases.get_usecase = lambda *_args, **_kwargs: _StubUseCase()


def reload_blueprint_modules() -> None:
    for module_name in (
        "main",
        "bootstrap",
        "app.layer4_frameworks.config.app_config",
        "app.layer4_frameworks.graph.graph_builder",
        "app.layer2_application.graphs.main_flow",
        "app.layer2_application.graphs.nodes.draft_node",
    ):
        sys.modules.pop(module_name, None)


@pytest.fixture
def isolated_blueprint_env(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Keep the container hermetic: the SDK builds an HttpToolRegistry and syncs
    # tools over HTTP at boot whenever REGISTRY_URL is set (it defaults to a dev
    # URL). With no registry reachable in CI that raises RegistrationError, so
    # blank it out and let the agent run with its local tools only.
    monkeypatch.setenv("REGISTRY_URL", "")
    reload_blueprint_modules()
    install_usecase_package_stubs()
    return monkeypatch


@pytest.fixture
def bootstrap_module(isolated_blueprint_env):
    return importlib.import_module("bootstrap")


@pytest.fixture
def main_module(isolated_blueprint_env):
    return importlib.import_module("main")


@pytest.fixture
def app_container(bootstrap_module):
    return bootstrap_module.build_app_container()


@pytest.fixture
def app_instance(main_module):
    return main_module.create_app()


@pytest.fixture
def client(app_instance):
    return TestClient(app_instance)
