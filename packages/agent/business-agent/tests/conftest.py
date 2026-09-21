import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


_BUSINESS_AGENT_ROOT = Path(__file__).resolve().parents[1]
_SDK_ROOT = _BUSINESS_AGENT_ROOT.parent / "agent-sdk"

if str(_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_SDK_ROOT))

os.environ["LLM_MODEL"] = ""
os.environ["OPENAI_API_KEY"] = ""


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
        "agent_sdk.bootstrap",
        "agent_sdk.layer4_frameworks.config.app_config",
        "app.layer4_frameworks.config.app_config",
        "app.layer4_frameworks.graph.graph_builder",
        "app.layer2_application.graphs.main_flow",
        "app.layer2_application.graphs.nodes.execute_node",
        "app.layer2_application.graphs.nodes.plan_usecase",
        "app.layer2_application.graphs.nodes.review_node",
    ):
        sys.modules.pop(module_name, None)


def stub_hana_connections(monkeypatch) -> None:
    """Keep bootstrap DB wiring in the container without a live HANA.

    build_app_container() calls setup() on the HANA repositories, which opens a
    real connection. Unit smoke tests only assert the container assembles, so we
    hand every repo a fake connection instead of reaching localhost:39041.
    """
    from agent_sdk.layer4_frameworks.persistence.hana.hana_connection_manager import (
        HanaConnectionManager,
    )

    monkeypatch.setattr(
        HanaConnectionManager, "_create_connection", lambda self: MagicMock()
    )


@pytest.fixture
def isolated_blueprint_env(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    stub_hana_connections(monkeypatch)
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
