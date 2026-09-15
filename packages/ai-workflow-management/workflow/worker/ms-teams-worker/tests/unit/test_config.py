"""Tests for configuration settings."""

from app.layer4_frameworks.config import Settings, settings


class TestSettings:
    """Tests for Settings class."""

    def test_defaults(self):
        """Test default settings values."""
        s = Settings()
        assert s.APP_NAME == "MS Teams Worker"
        assert s.WORKER_TYPE == "ms_teams"
        assert s.WORKER_NAME == "Microsoft Teams"
        assert s.WORKER_NODE_CLASS == "BUSINESS"
        assert s.WORKER_ICON == "MessageSquare"
        assert s.WORKER_COLOR == "#6264A7"
        assert s.SERVER_PORT == 38000

    def test_singleton_settings(self):
        """Test that settings is a singleton instance."""
        assert settings.WORKER_TYPE == "ms_teams"
        assert settings.SERVER_PORT == 38000
