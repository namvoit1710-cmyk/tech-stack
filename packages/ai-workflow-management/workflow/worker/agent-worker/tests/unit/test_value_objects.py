from app.layer1_domain.value_objects.ai_provider import AiProvider
from app.layer1_domain.value_objects.ai_role import AiRole
from app.layer1_domain.value_objects.response_format import ResponseFormat


class TestAiProvider:
    def test_openai_value(self):
        assert AiProvider.OPENAI.value == "openai"

    def test_all_members(self):
        assert set(AiProvider) == {AiProvider.OPENAI}

    def test_from_string(self):
        assert AiProvider("openai") == AiProvider.OPENAI


class TestAiRole:
    def test_system_value(self):
        assert AiRole.SYSTEM.value == "system"

    def test_user_value(self):
        assert AiRole.USER.value == "user"

    def test_assistant_value(self):
        assert AiRole.ASSISTANT.value == "assistant"

    def test_all_members(self):
        assert set(AiRole) == {AiRole.SYSTEM, AiRole.USER, AiRole.ASSISTANT}


class TestResponseFormat:
    def test_text_value(self):
        assert ResponseFormat.TEXT.value == "text"

    def test_json_value(self):
        assert ResponseFormat.JSON.value == "json"

    def test_all_members(self):
        assert set(ResponseFormat) == {ResponseFormat.TEXT, ResponseFormat.JSON}
