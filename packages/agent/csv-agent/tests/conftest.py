import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


class _FakeSettings:
    """Lightweight settings replacement for unit tests."""

    def __init__(self):
        self.FILE_URL = "http://file-service"
        self.DATA_FACTORY_URL = "http://localhost:8088/mcp/http/mcp"
        self.SCHEMA_TRANSFORM_QUERY_DATA_URL = (
            "http://schema-service/api/v1/query/{file_id}"
        )
        self.WORKFLOW_API_BASE_URL = "http://workflow-service"
        self.FILE_SERVICE_UPLOAD_URL = "http://file-service/api/v1/upload"
        self.REQUEST_TIMEOUT_SECONDS = 30
        self.OPENAI_API_KEY = "test-key"
        self.LLM_MODEL = "test-model"
        self.LLM_PROVIDER = ""
        self.LLM_TEMPERATURE = ""
        self.LLM_TIMEOUT = ""
        self.DEFAULT_TIMEOUT_SECONDS = ""
        self.AGENT_TYPE = "csv-agent"
        self.APP_NAME = "CSV Agent"
        self.DESCRIPTION = "CSV test agent"
        self.AGENT_ID = "csv-agent"
        self.SERVER_HOST = "127.0.0.1"
        self.SERVER_PORT = 36002
        self.MESSAGING_MODE = ""
        self.INFRA_MODE = "mock"
        self.QUEUE_NAME = "csv-agent"
        self.QUEUE_REQUEST_TOPIC = "agent.request.csv"
        self.QUEUE_REPLY_TOPIC = "agent.responses"
        self.QUEUE_REQUEST_MESSAGE_TYPE = "agent.request.agent"
        self.QUEUE_RESPONSE_MESSAGE_TYPE = "agent.response"
        self.DELEGATION_REQUEST_TOPIC = "agent.request.csv"
        self.DELEGATION_REQUEST_MESSAGE_TYPE = "agent.request.agent"
        self.DELEGATION_RESPONSE_MESSAGE_TYPE = "agent.response"
        self.KAFKA_GROUP_ID = "csv-agent-consumer"
        self.KAFKA_REQUEST_TOPIC = "agent.request.csv"
        self.KAFKA_RESPONSE_TOPIC = "agent.responses"
        self.EVENT_MESH_REQUEST_TOPIC = "agent.request.csv"
        self.EVENT_MESH_REPLY_TOPIC = "agent.responses"
        self.REGISTRY_URL = ""
        self.REGISTRY_API_URL = ""
        self.AGENT_KIND = "TECHNICAL"
        self.IS_PUBLISHED = True
        # Keep main.py import side-effect free in tests; the fixture calls create_app() explicitly.
        self.APP_MODE = "CONSUMER"
        self.AGENT_VERSION = "1.0.0"
        self.SDK_VERSION = "test"
        self.AGENT_DOMAIN = "data-processing"
        self.CAPABILITIES = ["csv_processing"]
        self.TOOL_ROOT_PACKAGE = "app.layer2_application.tools"
        self.DEFAULT_MAX_REVIEW_ITERATIONS = 3
        self.DEFAULT_FILENAME = "converted_data.csv"
        self.DEFAULT_MAX_WORKERS = 10
        self.MIN_GROUPS_FOR_PARALLEL = 2


sys.modules.setdefault(
    "app.layer4_frameworks.config.settings_adapter",
    SimpleNamespace(
        Settings=_FakeSettings,
        AppSettingsAdapter=_FakeSettings,
        settings=_FakeSettings(),
    ),
)


class _FakeRuntimeSettingsProvider:
    def __init__(self, settings):
        self._settings = settings

    def get_workflow_api_base_url(self) -> str:
        return str(self._settings.WORKFLOW_API_BASE_URL).rstrip("/")

    def get_schema_transform_query_data_url(self, file_id: str) -> str:
        return str(self._settings.SCHEMA_TRANSFORM_QUERY_DATA_URL).format(
            file_id=file_id
        )

    def get_file_service_upload_url(self) -> str:
        return str(self._settings.FILE_SERVICE_UPLOAD_URL).rstrip("/")

    def get_file_download_url(self, file_id: str) -> str:
        base_url = self.get_file_service_base_url()
        return f"{base_url}/api/v1/download/{file_id}"

    def get_file_service_base_url(self) -> str:
        return str(self._settings.FILE_URL).rstrip("/")

    def get_request_timeout_seconds(self) -> int:
        return int(self._settings.REQUEST_TIMEOUT_SECONDS)

    def get_default_max_review_iterations(self) -> int:
        return int(self._settings.DEFAULT_MAX_REVIEW_ITERATIONS)

    def get_default_filename(self) -> str:
        return str(self._settings.DEFAULT_FILENAME)

    def get_default_max_workers(self) -> int:
        return int(self._settings.DEFAULT_MAX_WORKERS)

    def get_min_groups_for_parallel(self) -> int:
        return int(self._settings.MIN_GROUPS_FOR_PARALLEL)

    def get_data_factory_url(self) -> str:
        return str(self._settings.DATA_FACTORY_URL)


class _FakeFilePreviewGateway:
    def get_odata_preview(self, file_id: str, num_rows: int):
        return {"file_id": file_id, "total_matches": 0, "data": []}

    def query_odata_data(self, file_id: str, odata_query: str = ""):
        return {
            "file_id": file_id,
            "total_matches": 0,
            "data": [],
            "odata_query": odata_query,
        }

    def download_file_content(self, file_id: str) -> bytes:
        return b""


class _FakeWorkflowNodeGateway:
    async def get_table_field_key(self, run_id: str, node_id: str):
        return None

    async def assign_file_to_node(
        self, run_id: str, node_id: str, file_id: str, target_field=None
    ):
        return {
            "message": "Upload file successfully assigned to node input",
            "file_id": file_id,
        }


class _FakeCsvJsonGateway:
    def fetch_csv_data(self, file_id: str):
        return []

    def upload_json_file(self, file_name: str, content: bytes) -> str:
        return "file_json_test"


class _FakeFileUploadGateway:
    def upload_file(self, file_path: str):
        return {
            "file_id": "file_uploaded_test",
            "download_url": "http://file-service/api/v1/download/file_uploaded_test",
        }


class MockOpenAIService:
    """Small fake SDK LLM service for tests that boot the full app container."""

    def get_chat_client(self):
        return self

    async def get_chat_completion(self, *args, **kwargs):
        return '{"header_row_index": 0, "skip_after_header": 0}'

    async def acomplete_with_format(self, *args, response_model=None, **kwargs):
        if response_model is not None:
            try:
                return response_model()
            except Exception:
                pass
        return {}


class MockHeaderInferenceService:
    async def get_header_by_llm(self, *args, **kwargs):
        return {
            "header": {
                "header_row_index": 0,
                "skip_after_header": 0,
                "header_columns": [],
            }
        }


@pytest.fixture
def client(monkeypatch):
    async def _sync_tools_noop(self, registrations):
        return {}

    try:
        monkeypatch.setattr(
            "agent_sdk.layer4_frameworks.registry.http_tool_registry.HttpToolRegistry.sync_tools",
            _sync_tools_noop,
            raising=False,
        )
    except (AttributeError, ImportError, ModuleNotFoundError):
        # Some isolated unit-test environments use a minimal agent_sdk stub.
        # The production SDK path exists in normal repo test runs.
        pass

    monkeypatch.setattr(
        "agent_sdk.layer4_frameworks.ai.llm_factory.make_llm_service",
        lambda *args, **kwargs: MockOpenAIService(),
    )

    # Import app factory after test settings shim is registered.
    from main import create_app

    return TestClient(create_app())
