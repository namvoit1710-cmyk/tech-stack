import importlib
import sys

import pytest
from fastapi.testclient import TestClient


class _StubUseCase:
    async def execute(self, *_args, **_kwargs):
        return {}


_BLUEPRINT_MODULES = (
    "main",
    "bootstrap",
    "app.layer4_frameworks.config.app_config",
    "app.layer4_frameworks.graph.graph_builder",
    "app.layer2_application.graphs.main_flow",
    "app.layer2_application.graphs.nodes.collect_node",
    "app.layer2_application.graphs.nodes.diagnose_node",
    "app.layer2_application.graphs.nodes.report_node",
)


def _reload_blueprint_modules() -> None:
    """Drop the blueprint modules so they re-import fresh (and re-bind `get_usecase`)."""
    for module_name in _BLUEPRINT_MODULES:
        sys.modules.pop(module_name, None)


def install_usecase_package_stubs(monkeypatch) -> None:
    usecases = importlib.import_module(
        "app.layer2_application.features.troubleshoot.use_cases"
    )
    # monkeypatch.setattr auto-restores after the test, so the stubs never leak into
    # tests that use the real use cases (e.g. the compiled-graph integration test).
    monkeypatch.setattr(usecases, "load_features", lambda logger: None)
    monkeypatch.setattr(usecases, "get_usecase", lambda *_args, **_kwargs: _StubUseCase())


@pytest.fixture
def isolated_blueprint_env(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("REGISTRY_URL", "")   # offline: no registry sync during tests
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _reload_blueprint_modules()
    install_usecase_package_stubs(monkeypatch)
    yield monkeypatch
    # Teardown: the node modules imported above bound to the stubbed `get_usecase`
    # (monkeypatch only restores the package attribute, not those local bindings). Drop them
    # so the next test re-imports against the restored, real use cases.
    _reload_blueprint_modules()


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
