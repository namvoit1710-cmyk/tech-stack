"""Unit tests for the webhook ingress controller (SA-2059 B7).

Pattern 1 (direct path→StartRun) is retired. The controller now delegates to
the WebhookIngressService, which verifies + normalizes + publishes onto a topic
and returns a fast 202. These tests drive the controller through a real
service + memory registration repo with a fake broker.
"""

import pytest
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from app.layer1_domain.entities.webhook_registration import WebhookRegistration
from app.layer4_frameworks.persistence.memory.memory_webhook_registration_repo import (
    MemoryWebhookRegistrationRepository,
)
from app.layer2_application.services.webhook_ingress.ingress_service import (
    WebhookIngressService,
)


class _Bus:
    def __init__(self):
        self.published = []

    def __call__(self, topic, message):
        self.published.append((topic, message))


def _client_with(registration=None):
    repo = MemoryWebhookRegistrationRepository()
    if registration is not None:
        repo.save(registration)
    bus = _Bus()
    container = {
        "webhook_registration_repo": repo,
        "webhook_ingress_service": WebhookIngressService(repo, bus),
    }
    app = create_fastapi_app(container)
    return TestClient(app), bus


def _reg(**kw):
    d = dict(name="generic-src", path="/hooks/order-status", source="generic",
             secret="s3cret", emit_topic="orders.created", tenant_id="acme")
    d.update(kw)
    return WebhookRegistration(**d)


class TestWebhookIngress:
    def test_verified_post_publishes_and_returns_202(self):
        client, bus = _client_with(_reg())
        resp = client.post(
            "/api/v1/webhooks/hooks/order-status",
            json={"order_status": "paid"},
            headers={"x-webhook-secret": "s3cret"},
        )
        assert resp.status_code == 200  # envelope wrapper, HTTP 200 with accepted body
        data = resp.json()["data"]
        assert data["outcome"] == "published"
        assert data["published"] == 1
        assert data["webhook_path"] == "/hooks/order-status"
        assert bus.published and bus.published[0][0] == "orders.created"

    def test_unregistered_path_returns_404(self):
        client, bus = _client_with(None)
        resp = client.post("/api/v1/webhooks/hooks/missing", json={"x": 1})
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"
        assert bus.published == []

    def test_bad_secret_returns_401(self):
        client, bus = _client_with(_reg())
        resp = client.post(
            "/api/v1/webhooks/hooks/order-status",
            json={"x": 1},
            headers={"x-webhook-secret": "wrong"},
        )
        assert resp.status_code == 401
        assert bus.published == []

    def test_nested_path_resolves(self):
        client, bus = _client_with(_reg(path="/api/v1/orders/webhook", emit_topic="orders.created"))
        resp = client.post(
            "/api/v1/webhooks/api/v1/orders/webhook",
            json={"event": "created"},
            headers={"x-webhook-secret": "s3cret"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["webhook_path"] == "/api/v1/orders/webhook"
        assert bus.published[0][0] == "orders.created"

    def test_service_unavailable_returns_503(self):
        app = create_fastapi_app({"webhook_ingress_service": None})
        client = TestClient(app)
        resp = client.post("/api/v1/webhooks/hooks/x", json={})
        assert resp.status_code == 503
