"""Tests for configuration settings."""

from app.layer4_frameworks.config import Settings, settings


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.APP_NAME == "Database Connection Worker"
        assert s.WORKER_TYPE == "database_connection"
        assert s.SERVER_PORT == 37000

    def test_singleton_settings(self):
        assert settings.WORKER_TYPE == "database_connection"
        assert settings.SERVER_PORT == 37000
