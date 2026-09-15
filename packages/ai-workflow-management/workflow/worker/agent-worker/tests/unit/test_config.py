from app.layer4_frameworks.config import Settings


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.APP_NAME == "Agent Worker"
        assert s.WORKER_TYPE == "agent"
        assert s.WORKER_NODE_CLASS == "TECHNICAL"
        assert s.WORKER_ICON == "Bot"
        assert s.WORKER_COLOR == "#8B5CF6"

    def test_worker_name(self):
        s = Settings()
        assert s.WORKER_NAME == "AI Agent"

    def test_worker_description(self):
        s = Settings()
        assert "AI providers" in s.WORKER_DESCRIPTION

    def test_tags_list(self):
        s = Settings()
        tags = s.get_tags_list()
        assert "ai" in tags
        assert "agent" in tags
        assert "llm" in tags
        assert "openai" in tags
