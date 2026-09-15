"""Integration test for GET /metrics — Prometheus scrape endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container


@pytest.fixture
def client() -> TestClient:
    container = build_app_container()
    return TestClient(create_fastapi_app(container))


class TestMetricsEndpoint:
    def test_metrics_endpoint_returns_prometheus_content_type(self, client):
        r = client.get("/metrics")
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        # Prometheus exposition format is a text/plain variant.
        assert "text/plain" in ct

    def test_metrics_response_is_not_json_wrapped(self, client):
        """ResponseWrapperMiddleware only wraps application/json responses.
        Prometheus scrape output must remain raw text/plain."""
        r = client.get("/metrics")
        # No JSON envelope.
        assert not r.text.lstrip().startswith("{")

    def test_metrics_reflects_counter_after_emit(self, client):
        container = client.app.state.container
        monitor = container["monitor"]
        # Exercise an unused-name counter for isolation.
        monitor.increment("integration_test_counter", tags={"suite": "metrics"})
        body = client.get("/metrics").text
        assert "integration_test_counter_total" in body
        assert 'suite="metrics"' in body

    def test_legacy_palette_counter_present_after_call(self, client):
        # Hit the legacy endpoint, then scrape.
        client.get("/api/v1/node-palette")
        body = client.get("/metrics").text
        assert "node_palette_legacy_requests_total" in body

    def test_catalog_detail_counter_labels_present_after_call(self, client):
        from app.layer4_frameworks.config.app_config import settings
        from app.layer3_adapters.controllers.restful.v1.tenant_gating import (
            reset_allowlist_cache,
        )
        # Enable v2 so the detail endpoint serves; rebuild app.
        settings.ENABLE_NODE_CATALOG_V2 = True
        settings.NODE_CATALOG_V2_TENANT_ALLOWLIST = ""
        reset_allowlist_cache()
        container = build_app_container()
        app = create_fastapi_app(container)
        c = TestClient(app)

        c.get("/api/v2/node-catalog/builtin:condition")
        body = c.get("/metrics").text
        assert "node_catalog_detail_requests_total" in body
        assert 'provider="builtin"' in body
        assert 'status="ok"' in body
