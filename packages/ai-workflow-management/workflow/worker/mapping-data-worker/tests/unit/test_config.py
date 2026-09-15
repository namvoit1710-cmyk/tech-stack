from app.layer4_frameworks.config import Settings


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.APP_NAME == "Mapping Data Worker"
        assert s.WORKER_TYPE == "mapping_data"
        assert s.WORKER_NODE_CLASS == "TECHNICAL"
        assert s.WORKER_ICON == "ArrowLeftRight"
        assert s.WORKER_COLOR == "#F59E0B"

    def test_tags_list(self):
        s = Settings()
        tags = s.get_tags_list()
        assert "mapping" in tags
        assert "transform" in tags
        assert "data" in tags
        assert "technical" in tags
