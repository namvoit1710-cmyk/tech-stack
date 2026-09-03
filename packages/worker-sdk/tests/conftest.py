import pytest
from fastapi.testclient import TestClient

from worker_sdk.layer3_adapters.controllers.worker_server import create_worker_app
from worker_sdk.bootstrap import build_app_container


@pytest.fixture
def app_container():
    return build_app_container()


@pytest.fixture
def client(app_container):
    app = create_worker_app(app_container)
    return TestClient(app)
