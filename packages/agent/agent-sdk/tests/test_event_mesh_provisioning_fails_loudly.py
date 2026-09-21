"""A mis-pointed EVENT_MESH_MANAGEMENT_URL must fail loudly, not silently.

Regression (dev, 2026-09-16): EVENT_MESH_MANAGEMENT_URL was set to the *messaging*
host, which does not serve /hub/rest/... at all and answers 404. `ensure_queue`
passed `allow_404=True`, so the PUT was swallowed, the queue was never created, and
`_consume_loop` 404-ed every poll for weeks with no log line naming the cause.
"""

import httpx
import pytest

from agent_sdk.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)


def _client(status: int) -> EventMeshBrokerClient:
    client = EventMeshBrokerClient(
        token_url="https://auth.example/oauth/token",
        messaging_url="https://messaging.example",
        management_url="https://management.example",
        client_id="cid",
        client_secret="sec",
        namespace="ns/event/core",
    )
    # Skip the token round trip; we are asserting on status handling only.
    client._access_token = "token"
    client._token_expires_at = float("inf")
    client._http = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(status, request=req))
    )
    return client


def test_ensure_queue_raises_on_404():
    with pytest.raises(httpx.HTTPStatusError):
        _client(404).ensure_queue("ns/event/core/profile.topic")


def test_ensure_queue_subscription_raises_on_404():
    with pytest.raises(httpx.HTTPStatusError):
        _client(404).ensure_queue_subscription(
            "ns/event/core/profile.topic", "profile.topic"
        )


def test_ensure_queue_accepts_409_already_exists():
    _client(409).ensure_queue("ns/event/core/profile.topic")


def test_management_base_url_targets_the_hub_management_path():
    assert _client(200)._management_base_url.endswith(
        "/hub/rest/api/v1/management/messaging"
    )
