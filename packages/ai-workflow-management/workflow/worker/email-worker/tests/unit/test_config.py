"""Tests for Email worker configuration."""

from app.layer4_frameworks.config import Settings


class TestSettings:
    """Tests for worker settings defaults."""

    def test_worker_identity(self):
        """Test the worker identity fields."""
        settings = Settings()
        assert settings.WORKER_TYPE == "email"
        assert settings.APP_NAME == "Email Worker"

    def test_node_appearance(self):
        """Test the workflow designer node fields."""
        settings = Settings()
        assert settings.WORKER_NODE_CLASS == "BUSINESS"
        assert settings.WORKER_ICON == "Mail"

    def test_server_port_is_unique_default(self):
        """Test the worker's own server port default."""
        settings = Settings()
        assert settings.SERVER_PORT == 38001

    def test_graph_base_url_default(self):
        """Test the Graph endpoint default (override for sovereign clouds)."""
        settings = Settings()
        assert settings.GRAPH_BASE_URL == "https://graph.microsoft.com/v1.0"
