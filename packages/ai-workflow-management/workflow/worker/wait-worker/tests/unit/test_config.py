from app.layer4_frameworks.config import Settings, settings


class TestConfig:
    def test_default_settings(self):
        s = Settings()
        assert s.APP_NAME == "Wait worker"
        assert s.WORKER_TYPE == "wait"
        assert s.WORKER_VERSION == "0.1.0"
        assert s.SDK_VERSION == "1.0.0"
        assert s.APP_MODE == "SERVER"
        assert s.SERVER_PORT == 35000
        assert s.SERVER_HOST == "localhost"
        assert s.HEARTBEAT_INTERVAL_SECONDS == 30

    def test_singleton_settings(self):
        assert settings.WORKER_TYPE == "wait"
        assert settings.APP_NAME == "Wait worker"
