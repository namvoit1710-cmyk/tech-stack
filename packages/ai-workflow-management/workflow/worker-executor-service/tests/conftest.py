import sys
import os
import pytest
from fastapi.testclient import TestClient

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Alias the generated proto package so `workflow_proto` (production import
# path, set up by the Docker build) resolves to the local `workflow` package
# under proto/generated during tests.
_proto_generated = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "proto", "generated")
)
if os.path.isdir(_proto_generated) and _proto_generated not in sys.path:
    sys.path.insert(0, _proto_generated)
    try:
        import workflow as _workflow
        import workflow.v1 as _workflow_v1
        sys.modules.setdefault("workflow_proto", _workflow)
        sys.modules.setdefault("workflow_proto.v1", _workflow_v1)
    except ImportError:
        pass

from main import create_app
from bootstrap import build_app_container


@pytest.fixture
def app_container():
    return build_app_container()


@pytest.fixture
def client(app_container):
    app = create_app()
    app.state.container = app_container
    return TestClient(app)
