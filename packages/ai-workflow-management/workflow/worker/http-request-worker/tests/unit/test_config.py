from app.layer4_frameworks.config import Settings, settings


class TestConfig:
    def test_default_settings(self):
        s = Settings()
        assert s.APP_NAME == "HTTP Request Worker"
        assert s.WORKER_TYPE == "http_request"
        assert s.WORKER_VERSION == "0.1.0"
        assert s.SDK_VERSION == "1.0.0"
        assert s.SERVER_PORT == 35000

    def test_singleton_settings(self):
        assert settings.WORKER_TYPE == "http_request"
