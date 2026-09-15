from app.layer4_frameworks.config import settings


class TestConfig:
    def test_worker_type(self):
        assert settings.WORKER_TYPE == "code"

    def test_app_name(self):
        assert settings.APP_NAME == "Code Worker"

    def test_server_port(self):
        assert settings.SERVER_PORT == 35000
