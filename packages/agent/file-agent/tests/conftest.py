import pytest
from fastapi.testclient import TestClient


class MockOpenAIService:
    async def get_chat_completion(self, *args, **kwargs):
        return '{"header_row_index": 0, "skip_after_header": 0}'

    def get_chat_client(self):
        return self


class MockHeaderInferenceService:
    async def get_header_by_llm(self, *args, **kwargs):
        return {
            "header": {
                "header_row_index": 0,
                "skip_after_header": 0,
                "header_columns": []
            }
        }


@pytest.fixture
def client(monkeypatch):
    # 🔥 Patch BEFORE importing create_app
    import bootstrap

    monkeypatch.setattr(
        bootstrap,
        "OpenAIService",
        lambda: MockOpenAIService()
    )

    monkeypatch.setattr(
        bootstrap,
        "HeaderInferenceService",
        lambda: MockHeaderInferenceService()
    )

    # Now import AFTER patching
    from main import create_app

    return TestClient(create_app())